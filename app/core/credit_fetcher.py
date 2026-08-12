from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from cryptography.exceptions import InvalidTag
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.core.security import decrypt_api_key
from app.db.models import ApiKey, CreditSnapshot
from app.errors import FcamError

logger = logging.getLogger(__name__)


def _parse_datetime(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass(frozen=True)
class CreditFetchResult:
    """In-memory credit probe result — safe to produce concurrently, apply serially."""

    api_key_id: int
    kind: str  # success | decrypt_failed | invalid | cooling | error
    remaining_credits: int = 0
    plan_credits: int = 0
    billing_period_start: datetime | None = None
    billing_period_end: datetime | None = None
    error_message: str | None = None
    http_status: int | None = None
    fetched_at: datetime | None = None


async def probe_credit_from_firecrawl(
    *,
    api_key_id: int,
    plaintext_api_key: str,
    config: AppConfig,
    request_id: str,
) -> CreditFetchResult:
    """
    HTTP-only credit probe. No DB access — suitable for concurrent workers.
    Results should be applied later via apply_credit_fetch_result (serial writer).
    """
    from app.core.urlutil import strip_provider_version_suffix

    base_url, _ = strip_provider_version_suffix(config.firecrawl.base_url)
    url = f"{base_url}/v2/team/credit-usage"
    headers = {
        "Authorization": f"Bearer {plaintext_api_key}",
        "X-Request-Id": request_id,
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(max(int(config.firecrawl.timeout), 1))
    now = datetime.now(timezone.utc)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="error",
            error_message="Request timeout",
            http_status=504,
            fetched_at=now,
        )
    except Exception as exc:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="error",
            error_message=f"{type(exc).__name__}: {exc}",
            fetched_at=now,
        )

    if response.status_code == 200:
        try:
            payload = response.json()
            if asyncio.iscoroutine(payload):
                payload = await payload
        except Exception:
            return CreditFetchResult(
                api_key_id=api_key_id,
                kind="error",
                error_message="Invalid JSON response",
                http_status=200,
                fetched_at=now,
            )

        if not payload.get("success"):
            return CreditFetchResult(
                api_key_id=api_key_id,
                kind="error",
                error_message=str(payload.get("error") or "Upstream returned success=false"),
                http_status=200,
                fetched_at=now,
            )

        data = payload.get("data") or {}
        remaining = int(data.get("remainingCredits") or 0)
        plan = int(data.get("planCredits") or 0)
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="success",
            remaining_credits=remaining,
            plan_credits=plan,
            billing_period_start=_parse_datetime(data.get("billingPeriodStart")),
            billing_period_end=_parse_datetime(data.get("billingPeriodEnd")),
            fetched_at=now,
        )

    if response.status_code in {401, 403}:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="invalid",
            error_message=f"{response.status_code} {getattr(response, 'text', '')}",
            http_status=int(response.status_code),
            fetched_at=now,
        )

    if response.status_code == 429:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="cooling",
            error_message="429 Rate Limited",
            http_status=429,
            fetched_at=now,
        )

    return CreditFetchResult(
        api_key_id=api_key_id,
        kind="error",
        error_message=f"{response.status_code} {getattr(response, 'text', '')}",
        http_status=int(response.status_code),
        fetched_at=now,
    )


def probe_credit_from_firecrawl_sync(
    *,
    api_key_id: int,
    plaintext_api_key: str,
    config: AppConfig,
    request_id: str,
    timeout_seconds: float | None = None,
) -> CreditFetchResult:
    """
    Synchronous HTTP credit probe for request-path use (e.g. immediate refresh after 4xx).
    Same semantics as probe_credit_from_firecrawl; no DB access.
    """
    from app.core.urlutil import strip_provider_version_suffix

    base_url, _ = strip_provider_version_suffix(config.firecrawl.base_url)
    url = f"{base_url}/v2/team/credit-usage"
    headers = {
        "Authorization": f"Bearer {plaintext_api_key}",
        "X-Request-Id": request_id,
        "Accept": "application/json",
    }
    # Keep 4xx-path refresh snappy so client latency does not spike too hard.
    base_to = max(int(getattr(config.firecrawl, "timeout", 30) or 30), 1)
    to = float(timeout_seconds) if timeout_seconds is not None else min(float(base_to), 8.0)
    timeout = httpx.Timeout(max(to, 1.0))
    now = datetime.now(timezone.utc)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="error",
            error_message="Request timeout",
            http_status=504,
            fetched_at=now,
        )
    except Exception as exc:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="error",
            error_message=f"{type(exc).__name__}: {exc}",
            fetched_at=now,
        )

    if response.status_code == 200:
        try:
            payload = response.json()
        except Exception:
            return CreditFetchResult(
                api_key_id=api_key_id,
                kind="error",
                error_message="Invalid JSON response",
                http_status=200,
                fetched_at=now,
            )

        if not payload.get("success"):
            return CreditFetchResult(
                api_key_id=api_key_id,
                kind="error",
                error_message=str(payload.get("error") or "Upstream returned success=false"),
                http_status=200,
                fetched_at=now,
            )

        data = payload.get("data") or {}
        remaining = int(data.get("remainingCredits") or 0)
        plan = int(data.get("planCredits") or 0)
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="success",
            remaining_credits=remaining,
            plan_credits=plan,
            billing_period_start=_parse_datetime(data.get("billingPeriodStart")),
            billing_period_end=_parse_datetime(data.get("billingPeriodEnd")),
            fetched_at=now,
        )

    if response.status_code in {401, 403}:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="invalid",
            error_message=f"{response.status_code} {getattr(response, 'text', '')}",
            http_status=int(response.status_code),
            fetched_at=now,
        )

    if response.status_code == 429:
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="cooling",
            error_message="429 Rate Limited",
            http_status=429,
            fetched_at=now,
        )

    return CreditFetchResult(
        api_key_id=api_key_id,
        kind="error",
        error_message=f"{response.status_code} {getattr(response, 'text', '')}",
        http_status=int(response.status_code),
        fetched_at=now,
    )


# Per-key debounce for request-path 4xx → credit refresh (monotonic seconds).
_4xx_refresh_lock = threading.Lock()
_4xx_refresh_last_mono: dict[int, float] = {}
_DEFAULT_4XX_REFRESH_DEBOUNCE_SECONDS = 10.0


def refresh_credits_after_4xx(
    *,
    db: Session,
    key: ApiKey,
    master_key: bytes | None,
    config: AppConfig,
    request_id: str | None = None,
    status_code: int | None = None,
    debounce_seconds: float | None = None,
) -> CreditFetchResult | None:
    """
    Immediately probe Firecrawl credits for one key after an upstream 4xx, apply to DB.

    Updates cached_remaining_credits / last_credit_check_at so the next KeyPool.select
    recomputes score from fresh data. Debounced per key to avoid stampeding credit API.

    Returns the probe result, or None if skipped (debounce / not firecrawl / no master key).
    Never raises to callers — logs and returns None on failure.
    """
    import time

    if master_key is None:
        return None
    if (getattr(key, "provider", None) or "firecrawl") != "firecrawl":
        return None
    if not getattr(key, "is_active", True) and (getattr(key, "status", "") or "").lower() in {
        "disabled",
        "decrypt_failed",
    }:
        return None

    kid = int(key.id)
    deb = float(
        debounce_seconds
        if debounce_seconds is not None
        else _DEFAULT_4XX_REFRESH_DEBOUNCE_SECONDS
    )
    now_m = time.monotonic()
    with _4xx_refresh_lock:
        last = _4xx_refresh_last_mono.get(kid)
        if last is not None and (now_m - last) < max(deb, 0.0):
            return None
        _4xx_refresh_last_mono[kid] = now_m

    rid = request_id or f"4xx-credit-refresh-{kid}-{int(time.time())}"
    try:
        plaintext = decrypt_api_key(master_key, key.api_key_ciphertext)
    except (InvalidTag, ValueError) as exc:
        result = CreditFetchResult(
            api_key_id=kid,
            kind="decrypt_failed",
            error_message=f"Decryption failed: {exc}",
            fetched_at=datetime.now(timezone.utc),
        )
        try:
            apply_credit_fetch_result(db=db, key=key, result=result, config=config)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "credit.4xx_refresh_decrypt_apply_failed",
                extra={"fields": {"api_key_id": kid, "status_code": status_code}},
            )
        return result

    try:
        result = probe_credit_from_firecrawl_sync(
            api_key_id=kid,
            plaintext_api_key=plaintext,
            config=config,
            request_id=rid,
        )
    except Exception:
        logger.exception(
            "credit.4xx_refresh_probe_failed",
            extra={"fields": {"api_key_id": kid, "status_code": status_code}},
        )
        return None

    try:
        apply_credit_fetch_result(db=db, key=key, result=result, config=config)
        # Touch last_credit_check_at even on non-success so freshness reflects the attempt
        # when success path already sets it; for cooling keep existing remaining.
        if result.kind == "success":
            db.commit()
            logger.info(
                "credit.4xx_refresh_ok",
                extra={
                    "fields": {
                        "api_key_id": kid,
                        "status_code": status_code,
                        "remaining": int(result.remaining_credits),
                        "plan": int(result.plan_credits),
                    }
                },
            )
        else:
            db.commit()
            logger.info(
                "credit.4xx_refresh_result",
                extra={
                    "fields": {
                        "api_key_id": kid,
                        "status_code": status_code,
                        "kind": result.kind,
                        "http_status": result.http_status,
                    }
                },
            )
    except Exception:
        db.rollback()
        logger.exception(
            "credit.4xx_refresh_apply_failed",
            extra={"fields": {"api_key_id": kid, "status_code": status_code}},
        )
        return result

    return result


def apply_credit_fetch_result(
    *,
    db: Session,
    key: ApiKey,
    result: CreditFetchResult,
    config: AppConfig,
) -> CreditSnapshot:
    """
    Persist one CreditFetchResult. Caller should serialize writes (esp. SQLite).
    Does not commit — caller commits (or rolls back) the session.
    """
    now = result.fetched_at or datetime.now(timezone.utc)

    if result.kind == "decrypt_failed":
        key.status = "decrypt_failed"
        key.is_active = False
        key.next_refresh_at = None
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=0,
            plan_credits=0,
            fetch_success=False,
            error_message=result.error_message or "Decryption failed",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    if result.kind == "invalid":
        key.status = "invalid"
        key.is_active = False
        key.next_refresh_at = None
        key.cached_remaining_credits = 0
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=0,
            plan_credits=0,
            fetch_success=False,
            error_message=result.error_message or "invalid key",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    if result.kind == "cooling":
        key.status = "cooling"
        key.cooldown_until = now
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=0,
            plan_credits=0,
            fetch_success=False,
            error_message=result.error_message or "429 Rate Limited",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    if result.kind == "success":
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=int(result.remaining_credits),
            plan_credits=int(result.plan_credits),
            billing_period_start=result.billing_period_start,
            billing_period_end=result.billing_period_end,
            fetch_success=True,
        )
        db.add(snapshot)
        db.flush()

        key.cached_remaining_credits = int(result.remaining_credits)
        key.cached_plan_credits = int(result.plan_credits)
        key.last_credit_snapshot_id = snapshot.id
        key.last_credit_check_at = now

        try:
            from app.core.credit_refresh import calculate_next_refresh_time

            key.next_refresh_at = calculate_next_refresh_time(key, config)
        except Exception:
            key.next_refresh_at = None

        return snapshot

    # error / unknown
    snapshot = CreditSnapshot(
        api_key_id=key.id,
        remaining_credits=0,
        plan_credits=0,
        fetch_success=False,
        error_message=result.error_message or "Upstream error",
    )
    db.add(snapshot)
    db.flush()
    return snapshot


async def fetch_credit_from_firecrawl(
    *,
    db: Session,
    key: ApiKey,
    master_key: bytes,
    config: AppConfig,
    request_id: str,
) -> CreditSnapshot:
    """
    Convenience: decrypt + probe + apply + commit (single-key / admin path).

    Background multi-worker refresh should use probe_credit_from_firecrawl +
    apply_credit_fetch_result instead, so HTTP can run concurrent and DB serial.
    """
    try:
        plaintext_api_key = decrypt_api_key(master_key, key.api_key_ciphertext)
    except (InvalidTag, ValueError) as exc:
        result = CreditFetchResult(
            api_key_id=key.id,
            kind="decrypt_failed",
            error_message=f"Decryption failed: {exc}",
            fetched_at=datetime.now(timezone.utc),
        )
        try:
            snapshot = apply_credit_fetch_result(db=db, key=key, result=result, config=config)
            db.commit()
        except Exception:
            db.rollback()
            raise
        raise FcamError(status_code=500, code="DECRYPTION_FAILED", message="Failed to decrypt API key") from exc

    result = await probe_credit_from_firecrawl(
        api_key_id=key.id,
        plaintext_api_key=plaintext_api_key,
        config=config,
        request_id=request_id,
    )

    try:
        snapshot = apply_credit_fetch_result(db=db, key=key, result=result, config=config)
        db.commit()
        db.refresh(snapshot)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "credit.snapshot_write_failed",
            extra={"fields": {"api_key_id": key.id, "request_id": request_id}},
        )
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    if result.kind == "success":
        return snapshot
    if result.kind == "invalid":
        raise FcamError(
            status_code=int(result.http_status or 401),
            code="INVALID_API_KEY",
            message="API key is invalid or unauthorized",
        )
    if result.kind == "cooling":
        raise FcamError(status_code=429, code="RATE_LIMITED", message="Firecrawl API rate limited")
    if result.kind == "error" and result.http_status == 504:
        raise FcamError(status_code=504, code="TIMEOUT", message="Firecrawl API request timeout")
    if result.kind == "error" and (result.error_message or "").startswith("Invalid JSON"):
        raise FcamError(status_code=502, code="UPSTREAM_ERROR", message="Invalid upstream JSON")
    if result.kind == "error" and "success=false" in (result.error_message or ""):
        raise FcamError(status_code=502, code="UPSTREAM_ERROR", message="Firecrawl credit usage failed")

    raise FcamError(
        status_code=int(result.http_status or 502),
        code="UPSTREAM_ERROR",
        message=f"Firecrawl API error: {result.http_status or result.error_message}",
    )
