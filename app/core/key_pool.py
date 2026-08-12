from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.core.cooldown import NoopCooldownStore
from app.core.time import now_utc
from app.db.models import ApiKey
from app.errors import FcamError

logger = logging.getLogger(__name__)

# Terminal statuses: never selected, never credit-refreshed (is_active also False).
DISABLED_STATUSES = frozenset({"disabled", "decrypt_failed", "invalid"})

# Soft-unavailable: may recover after cooldown ("failed" is used by transient failure threshold).
COOLING_STATUSES = frozenset({"cooling", "failed"})

# Reasons / tokens that count as 4xx for selection-score penalty (only 4xx reduce score).
_SCORE_4XX_REASON_TOKENS = frozenset(
    {
        "upstream_4xx",
        "http_4xx",
        "client_error",
        "4xx",
        "400",
        "401",
        "402",
        "403",
        "404",
        "405",
        "408",
        "409",
        "410",
        "413",
        "414",
        "415",
        "422",
        "425",
        "429",
        "451",
    }
)


def is_4xx_score_failure(*, reason: str = "", status_code: int | None = None) -> bool:
    """True when this failure should reduce selection score (4xx only)."""
    if status_code is not None:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            code = None
        else:
            if 400 <= code < 500:
                return True
            if code >= 500 or code < 400:
                # explicit non-4xx status wins over ambiguous reason strings
                if code != 0:
                    return False
    r = (reason or "").strip().lower()
    if not r:
        return False
    if r in _SCORE_4XX_REASON_TOKENS:
        return True
    # e.g. "upstream_4xx_422", "http_4xx"
    if "4xx" in r or "upstream_4" in r:
        return True
    # bare status embedded in reason
    for tok in r.replace("-", "_").split("_"):
        if tok.isdigit():
            try:
                c = int(tok)
            except ValueError:
                continue
            if 400 <= c < 500:
                return True
    return False


# Backward-compatible alias: network / transport reasons never score-penalize.
NETWORK_FAILURE_REASONS = frozenset(
    {
        "timeout",
        "http_error",
        "connect_error",
        "connection_error",
        "network",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "upstream_5xx",
        "5xx",
        "server_error",
    }
)


@dataclass
class SelectedKey:
    api_key: ApiKey
    today: object
    now: datetime
    score: float = 0.0
    explore: bool = False  # True when ε-greedy / rotation path picked this key


def _is_disabled(key: ApiKey) -> bool:
    if not key.is_active:
        return True
    status = (key.status or "").lower()
    return status in DISABLED_STATUSES


def _cooldown_remaining_seconds(key: ApiKey, now: datetime) -> int:
    if not key.cooldown_until:
        return 0
    cooldown_until = (
        key.cooldown_until
        if key.cooldown_until.tzinfo is not None
        else key.cooldown_until.replace(tzinfo=timezone.utc)
    )
    remaining = int((cooldown_until - now).total_seconds())
    return max(remaining, 0)


def score_key(
    key: ApiKey,
    now: datetime,
    *,
    freshness_half_life_seconds: float = 6 * 3600,
    unknown_credit_baseline: float = 50.0,
    non_network_failure_count: float = 0.0,
    failure_penalty_unit: float = 1.0,
) -> float | None:
    """
    Scientific selection score.

    - remaining_credits == 0  → ineligible (None)
    - higher remaining       → higher score (via log1p)
    - fresher last_credit_check_at → higher score
    - unknown credits        → treat unknown_credit_baseline as **assumed remaining credits**,
      then credit_weight = log1p(baseline)  (e.g. 50 → log1p(50), same scale as known keys)
    - recent **4xx** failures → multiplicative penalty (5xx / network ignored for score)

    score = credit_weight * freshness_weight * failure_weight
    freshness_weight = 1 / (1 + age / half_life)   ∈ (0, 1]
    failure_weight   = 1 / (1 + failures * unit)    ∈ (0, 1]
    """
    remaining = key.cached_remaining_credits
    if remaining is not None and int(remaining) <= 0:
        return None

    if remaining is None:
        # Config is in **credit units** (额度), not raw weight. weight = log1p(credits).
        assumed = max(float(unknown_credit_baseline), 0.0)
        credit_weight = math.log1p(assumed)
    else:
        # log1p softens extreme high-credit keys so medium keys still compete
        credit_weight = math.log1p(max(int(remaining), 0))

    last_check = key.last_credit_check_at
    if last_check is None:
        # Unknown freshness: mid weight — encourage a real refresh soon, but still usable
        freshness_weight = 0.55
    else:
        if last_check.tzinfo is None:
            last_check = last_check.replace(tzinfo=timezone.utc)
        age = max((now - last_check).total_seconds(), 0.0)
        half = max(float(freshness_half_life_seconds), 1.0)
        freshness_weight = 1.0 / (1.0 + age / half)

    fails = max(float(non_network_failure_count), 0.0)
    unit = max(float(failure_penalty_unit), 0.0)
    if unit <= 0 or fails <= 0:
        failure_weight = 1.0
    else:
        failure_weight = 1.0 / (1.0 + fails * unit)

    return float(credit_weight) * float(freshness_weight) * float(failure_weight)


class KeyPool:
    """
    Unified global upstream key pool (no per-client pools, no daily quota gating).

    Selection prefers keys with ample remaining credits and a fresh credit snapshot.
    Zero-credit and disabled/invalid keys are never selected.

    RPM is applied as a **hard schedule filter** (skip keys with no token left) — it does
    **not** subtract from the numeric score. Recent **4xx** failures reduce score (not 5xx/network).
    With probability epsilon, explore via weighted rotation among eligible keys.
    """

    def __init__(
        self,
        *,
        cooldown_store: object | None = None,
        runtime_settings: object | None = None,
        rate_limiter: object | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._cooldown_store = cooldown_store or NoopCooldownStore()
        self._rng = random.Random()
        self._runtime_settings = runtime_settings
        self._rate_limiter = rate_limiter
        # key_id -> (weighted_fail_count, last_mono_ts)
        self._nn_failures: dict[int, tuple[float, float]] = {}
        # per-provider round-robin cursor for explore path
        self._rr_cursor: dict[str, int] = {}

    def set_rate_limiter(self, rate_limiter: object | None) -> None:
        self._rate_limiter = rate_limiter

    def record_success(self, key_id: int) -> None:
        """Clear 4xx failure memory after a successful upstream call."""
        with self._lock:
            self._nn_failures.pop(int(key_id), None)

    def record_failure(
        self,
        key_id: int,
        reason: str = "",
        *,
        status_code: int | None = None,
    ) -> None:
        """
        Record a failure for scoring.

        Only **HTTP 4xx** (client errors) reduce selection score.
        5xx, timeouts, and other network/transport failures are ignored here
        (they may still feed separate cooldown / failed-threshold logic in the forwarder).
        """
        if not is_4xx_score_failure(reason=reason, status_code=status_code):
            return
        kid = int(key_id)
        now = time.monotonic()
        with self._lock:
            count, _ = self._nn_failures.get(kid, (0.0, now))
            self._nn_failures[kid] = (float(count) + 1.0, now)

    def _failure_count(self, key_id: int, *, half_life_seconds: float) -> float:
        """Exponentially decayed 4xx failure weight for scoring."""
        kid = int(key_id)
        now = time.monotonic()
        with self._lock:
            item = self._nn_failures.get(kid)
            if item is None:
                return 0.0
            count, last = item
            if count <= 0:
                return 0.0
            half = max(float(half_life_seconds), 1.0)
            age = max(now - last, 0.0)
            # Halve every half_life since last failure event (simple continuous decay)
            decayed = float(count) * (0.5 ** (age / half))
            if decayed < 0.05:
                self._nn_failures.pop(kid, None)
                return 0.0
            # refresh stored decayed value lazily
            self._nn_failures[kid] = (decayed, now)
            return decayed

    def _rpm_allows(self, key: ApiKey) -> bool:
        """
        Schedule-time RPM gate: if limiter says no capacity, skip key.
        Does not consume a token when peek() is available; falls back to True if unknown.
        """
        lim = self._rate_limiter
        if lim is None:
            return True
        rpm = int(getattr(key, "rate_limit_per_min", 0) or 0)
        if rpm <= 0:
            return True
        kid = str(key.id)
        peek = getattr(lim, "peek", None)
        if callable(peek):
            try:
                ok, _retry = peek(kid, rpm)
                return bool(ok)
            except Exception:
                return True
        # No peek → do not pre-filter (forwarder still enforces allow())
        return True

    def select(
        self,
        db: Session,
        config: AppConfig,
        *,
        client_id: int | None = None,  # kept for API compat; IGNORED (global pool)
        provider: str = "firecrawl",
        exclude_ids: set[int] | frozenset[int] | None = None,
    ) -> SelectedKey:
        del client_id  # unified pool — all clients share the same upstream keys
        now = now_utc()
        exclude = set(exclude_ids or ())

        try:
            keys = (
                db.query(ApiKey)
                .filter(ApiKey.provider == provider)
                .order_by(ApiKey.id.asc())
                .all()
            )
        except Exception as exc:
            logger.exception("db.keys_list_failed", extra={"fields": {"op": "keys_list"}})
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

        if not keys:
            raise FcamError(status_code=503, code="NO_KEY_CONFIGURED", message="No key configured")

        any_active = any(k.is_active and (k.status or "").lower() not in DISABLED_STATUSES for k in keys)
        if not any_active:
            raise FcamError(status_code=503, code="ALL_KEYS_DISABLED", message="All keys disabled")

        sched = getattr(config, "scheduling", None)
        cfg_half = int(getattr(sched, "freshness_half_life_seconds", 6 * 3600) or 6 * 3600)
        cfg_base = float(getattr(sched, "unknown_credit_baseline", 50.0) or 50.0)
        cfg_eps = float(getattr(sched, "epsilon_greedy", 0.1) if getattr(sched, "epsilon_greedy", None) is not None else 0.1)
        near_ratio = float(getattr(sched, "near_score_ratio", 0.95) or 0.95)
        near_ratio = min(max(near_ratio, 0.5), 1.0)
        fail_half = float(getattr(sched, "failure_penalty_half_life_seconds", 300) or 300)
        fail_unit = float(getattr(sched, "failure_penalty_unit", 1.0) or 1.0)

        if self._runtime_settings is not None and hasattr(self._runtime_settings, "effective_half_life"):
            half_life = float(self._runtime_settings.effective_half_life(cfg_half))
            unknown_baseline = float(self._runtime_settings.effective_unknown_baseline(cfg_base))
            if hasattr(self._runtime_settings, "effective_epsilon_greedy"):
                epsilon = float(self._runtime_settings.effective_epsilon_greedy(cfg_eps))
            else:
                epsilon = cfg_eps
        else:
            half_life = float(cfg_half)
            unknown_baseline = float(cfg_base)
            epsilon = cfg_eps
        epsilon = min(max(float(epsilon), 0.0), 1.0)

        cooling_retry_after: int | None = None
        cooling_seen = 0
        disabled_seen = 0
        zero_credit_seen = 0
        excluded_seen = 0
        rpm_blocked = 0
        candidates: list[tuple[float, ApiKey]] = []

        for key in keys:
            if key.id in exclude:
                excluded_seen += 1
                continue

            if _is_disabled(key):
                disabled_seen += 1
                continue

            status = (key.status or "").lower()
            if status in COOLING_STATUSES or status == "cooling" or key.cooldown_until:
                remaining = _cooldown_remaining_seconds(key, now)
                store_remaining = None
                if hasattr(self._cooldown_store, "remaining_seconds"):
                    try:
                        store_remaining = self._cooldown_store.remaining_seconds(key_id=key.id)  # type: ignore[arg-type]
                    except Exception:
                        store_remaining = None
                if store_remaining is not None:
                    remaining = max(remaining, int(store_remaining))
                if remaining > 0:
                    cooling_seen += 1
                    cooling_retry_after = (
                        remaining if cooling_retry_after is None else min(cooling_retry_after, remaining)
                    )
                    continue
                # cooldown expired → reactivate
                key.cooldown_until = None
                if status in COOLING_STATUSES or status == "cooling":
                    key.status = "active"

            # Zero remaining credits: never select
            if key.cached_remaining_credits is not None and int(key.cached_remaining_credits) <= 0:
                zero_credit_seen += 1
                continue

            # RPM: schedule filter only — no score subtraction
            if not self._rpm_allows(key):
                rpm_blocked += 1
                continue

            nn_fails = self._failure_count(int(key.id), half_life_seconds=fail_half)
            s = score_key(
                key,
                now,
                freshness_half_life_seconds=half_life,
                unknown_credit_baseline=unknown_baseline,
                non_network_failure_count=nn_fails,
                failure_penalty_unit=fail_unit,
            )
            if s is None:
                zero_credit_seen += 1
                continue
            candidates.append((s, key))

        if not candidates:
            n = len(keys)
            if (
                disabled_seen + excluded_seen >= n
                and disabled_seen > 0
                and zero_credit_seen == 0
                and cooling_seen == 0
                and rpm_blocked == 0
            ):
                raise FcamError(status_code=503, code="ALL_KEYS_DISABLED", message="All keys disabled")
            if (
                zero_credit_seen
                and zero_credit_seen + disabled_seen + excluded_seen + cooling_seen + rpm_blocked >= n
                and cooling_seen == 0
                and rpm_blocked == 0
            ):
                raise FcamError(
                    status_code=503,
                    code="ALL_KEYS_NO_CREDITS",
                    message="All keys have zero remaining credits",
                )
            if cooling_seen and cooling_seen + disabled_seen + excluded_seen + zero_credit_seen + rpm_blocked >= n:
                raise FcamError(
                    status_code=429,
                    code="ALL_KEYS_COOLING",
                    message="All keys cooling",
                    retry_after=cooling_retry_after,
                )
            if rpm_blocked and rpm_blocked + disabled_seen + excluded_seen + zero_credit_seen + cooling_seen >= n:
                raise FcamError(
                    status_code=429,
                    code="ALL_KEYS_RATE_LIMITED",
                    message="All keys rate-limited (RPM)",
                    retry_after=1,
                )
            raise FcamError(status_code=503, code="NO_KEY_AVAILABLE", message="No key available")

        explore = False
        with self._lock:
            roll = self._rng.random()

        # ε-greedy / rotation explore among all eligible (not only near-best)
        if epsilon > 0 and roll < epsilon and len(candidates) > 1:
            explore = True
            ordered = sorted(candidates, key=lambda t: t[1].id)
            with self._lock:
                cur = int(self._rr_cursor.get(provider, 0)) % len(ordered)
                self._rr_cursor[provider] = cur + 1
            chosen_score, chosen = ordered[cur]
        else:
            # Prefer highest score. Soft tie-break among near-equals.
            candidates.sort(key=lambda t: (-t[0], t[1].id))
            best_score = candidates[0][0]
            near = [(s, k) for s, k in candidates if s >= best_score * near_ratio]
            if len(near) == 1:
                chosen_score, chosen = near[0]
            else:
                total = sum(max(s, 0.0) for s, _ in near)
                if total <= 0:
                    chosen_score, chosen = near[0]
                else:
                    with self._lock:
                        r = self._rng.random() * total
                    acc = 0.0
                    chosen_score, chosen = near[0]
                    for s, k in near:
                        acc += max(s, 0.0)
                        if r <= acc:
                            chosen_score, chosen = s, k
                            break

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("db.key_state_commit_failed", extra={"fields": {"api_key_id": chosen.id}})
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

        return SelectedKey(
            api_key=chosen,
            today=None,
            now=now,
            score=float(chosen_score),
            explore=explore,
        )
