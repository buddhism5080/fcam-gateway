from __future__ import annotations

import logging
import math
import random
import threading
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


@dataclass
class SelectedKey:
    api_key: ApiKey
    today: object
    now: datetime
    score: float = 0.0


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
) -> float | None:
    """
    Scientific selection score.

    - remaining_credits == 0  → ineligible (None)
    - higher remaining       → higher score
    - fresher last_credit_check_at → higher score
    - unknown credits        → modest baseline so new keys still get tried

    score = credit_weight * freshness_weight
    freshness_weight = 1 / (1 + age / half_life)   ∈ (0, 1]
    """
    remaining = key.cached_remaining_credits
    if remaining is not None and int(remaining) <= 0:
        return None

    if remaining is None:
        credit_weight = float(unknown_credit_baseline)
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

    return float(credit_weight) * float(freshness_weight)


class KeyPool:
    """
    Unified global upstream key pool (no per-client pools, no daily quota gating).

    Selection prefers keys with ample remaining credits and a fresh credit snapshot.
    Zero-credit and disabled/invalid keys are never selected.
    """

    def __init__(self, *, cooldown_store: object | None = None, runtime_settings: object | None = None) -> None:
        self._lock = threading.Lock()
        self._cooldown_store = cooldown_store or NoopCooldownStore()
        self._rng = random.Random()
        self._runtime_settings = runtime_settings

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

        cfg_half = int(
            getattr(getattr(config, "scheduling", None), "freshness_half_life_seconds", 6 * 3600) or 6 * 3600
        )
        cfg_base = float(
            getattr(getattr(config, "scheduling", None), "unknown_credit_baseline", 50.0) or 50.0
        )
        if self._runtime_settings is not None and hasattr(self._runtime_settings, "effective_half_life"):
            half_life = float(self._runtime_settings.effective_half_life(cfg_half))
            unknown_baseline = float(self._runtime_settings.effective_unknown_baseline(cfg_base))
        else:
            half_life = float(cfg_half)
            unknown_baseline = float(cfg_base)

        cooling_retry_after: int | None = None
        cooling_seen = 0
        disabled_seen = 0
        zero_credit_seen = 0
        excluded_seen = 0
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

            s = score_key(
                key,
                now,
                freshness_half_life_seconds=half_life,
                unknown_credit_baseline=unknown_baseline,
            )
            if s is None:
                zero_credit_seen += 1
                continue
            candidates.append((s, key))

        if not candidates:
            n = len(keys)
            if disabled_seen + excluded_seen >= n and disabled_seen > 0 and zero_credit_seen == 0 and cooling_seen == 0:
                raise FcamError(status_code=503, code="ALL_KEYS_DISABLED", message="All keys disabled")
            if zero_credit_seen and zero_credit_seen + disabled_seen + excluded_seen + cooling_seen >= n and cooling_seen == 0:
                raise FcamError(
                    status_code=503,
                    code="ALL_KEYS_NO_CREDITS",
                    message="All keys have zero remaining credits",
                )
            if cooling_seen and cooling_seen + disabled_seen + excluded_seen + zero_credit_seen >= n:
                raise FcamError(
                    status_code=429,
                    code="ALL_KEYS_COOLING",
                    message="All keys cooling",
                    retry_after=cooling_retry_after,
                )
            raise FcamError(status_code=503, code="NO_KEY_AVAILABLE", message="No key available")

        # Prefer highest score (ample credits + fresh snapshot). Soft tie-break among near-equals.
        candidates.sort(key=lambda t: (-t[0], t[1].id))
        best_score = candidates[0][0]
        # Keys within 5% of best score are eligible for light jitter (load spread)
        near = [(s, k) for s, k in candidates if s >= best_score * 0.95]
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

        return SelectedKey(api_key=chosen, today=None, now=now, score=float(chosen_score))
