from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.core.key_pool import DISABLED_STATUSES
from app.core.security import decrypt_api_key
from app.db.models import ApiKey, CreditSnapshot

logger = logging.getLogger(__name__)


def calculate_next_refresh_time(key: ApiKey, config: AppConfig) -> datetime | None:
    """
    根据额度情况计算下次刷新时间。

    - 失效/禁用 key：返回 None（不再刷新）
    - 使用率 > 90%（剩余 < 10%）：30 分钟（可配 high_usage_interval）
    - 使用率 > 70%（剩余 10%-30%）：60 分钟
    - 使用率 > 50%（剩余 30%-50%）：120 分钟
    - 其他（剩余 >= 50%）：240 分钟
    - remaining >= abundant_remaining_threshold（默认 1000）：间隔至少 abundant_min_interval_minutes（默认 120）
    - plan=0：使用 fixed_refresh.interval_minutes
    - 缓存未初始化：立即刷新（now）
    - remaining=0 且 plan>0：等待到下个月 1 号（仍可刷新以捕获账期重置；但调度不会选中）
    """
    status = (key.status or "").lower()
    if (not key.is_active) or status in DISABLED_STATUSES:
        return None

    now = datetime.now(timezone.utc)

    remaining = key.cached_remaining_credits
    plan = key.cached_plan_credits
    if remaining is None or plan is None:
        return now

    plan_i = int(plan)
    remaining_i = int(remaining)

    if plan_i == 0:
        return now + timedelta(minutes=int(config.credit_monitoring.fixed_refresh.interval_minutes))

    if remaining_i == 0:
        next_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)
        return next_month.replace(day=1)

    usage_ratio = 1 - (remaining_i / plan_i)
    smart = config.credit_monitoring.smart_refresh

    if not smart.enabled:
        interval_minutes = int(config.credit_monitoring.fixed_refresh.interval_minutes)
    else:
        if usage_ratio > 0.9:
            interval_minutes = int(smart.high_usage_interval)
        elif usage_ratio > 0.7:
            interval_minutes = int(smart.medium_usage_interval)
        elif usage_ratio > 0.5:
            interval_minutes = int(smart.normal_usage_interval)
        else:
            interval_minutes = int(smart.low_usage_interval)

        # Abundant remaining: never refresh more often than the floor (default 120 min when remaining >= 1000).
        threshold = int(getattr(smart, "abundant_remaining_threshold", 1000) or 1000)
        floor_m = int(getattr(smart, "abundant_min_interval_minutes", 120) or 120)
        if remaining_i >= threshold:
            interval_minutes = max(interval_minutes, floor_m)

    return now + timedelta(minutes=interval_minutes)


async def credit_refresh_loop(
    *,
    db_factory: Callable[[], Session],
    master_key: bytes,
    config: AppConfig,
    stop_event: asyncio.Event,
    runtime_settings: object | None = None,
) -> None:
    """
    后台额度刷新循环：
    1) 查找到期 Key
    2) 多 worker 并发 HTTP 探活/拉额度（结果先落内存）
    3) 单写者串行写入 DB（SQLite 友好）
    失效 key 自动禁用且不再进入刷新队列。
    """
    logger.info(
        "credit.refresh_loop_started",
        extra={"fields": {"workers": int(getattr(config.credit_monitoring, "workers", 4) or 4)}},
    )

    while not stop_event.is_set():
        try:
            await _refresh_once(
                db_factory=db_factory,
                master_key=master_key,
                config=config,
                runtime_settings=runtime_settings,
            )
        except Exception:
            logger.exception("credit.refresh_loop_failed")

        interval = max(int(config.credit_monitoring.refresh_check_interval_seconds), 1)
        if runtime_settings is not None:
            fn = getattr(runtime_settings, "effective_credit_refresh_check_interval_seconds", None)
            if callable(fn):
                try:
                    interval = max(int(fn(interval)), 1)
                except Exception:
                    pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue

    logger.info("credit.refresh_loop_stopped")


async def _probe_one_key(
    *,
    key_id: int,
    ciphertext: bytes,
    master_key: bytes,
    config: AppConfig,
    now: datetime,
    sem: asyncio.Semaphore,
) -> object:
    """Concurrent HTTP probe; returns CreditFetchResult (no DB)."""
    from app.core.credit_fetcher import CreditFetchResult, probe_credit_from_firecrawl

    async with sem:
        try:
            plaintext = decrypt_api_key(master_key, ciphertext)
        except (InvalidTag, ValueError) as exc:
            return CreditFetchResult(
                api_key_id=key_id,
                kind="decrypt_failed",
                error_message=f"Decryption failed: {exc}",
                fetched_at=now,
            )

        request_id = f"credit-refresh-{key_id}-{int(now.timestamp())}"
        try:
            return await probe_credit_from_firecrawl(
                api_key_id=key_id,
                plaintext_api_key=plaintext,
                config=config,
                request_id=request_id,
            )
        except Exception as exc:
            logger.info(
                "credit.refresh_probe_failed",
                extra={"fields": {"api_key_id": key_id, "error": str(exc)}},
            )
            return CreditFetchResult(
                api_key_id=key_id,
                kind="error",
                error_message=f"{type(exc).__name__}: {exc}",
                fetched_at=now,
            )


def _apply_results_serial(
    *,
    db_factory: Callable[[], Session],
    config: AppConfig,
    results: list[object],
    now: datetime,
    retry_delay_minutes: int | None = None,
) -> None:
    """Single-writer path: apply in-memory probe results one key at a time."""
    from app.core.credit_fetcher import CreditFetchResult, apply_credit_fetch_result

    retry_m = max(
        int(
            retry_delay_minutes
            if retry_delay_minutes is not None
            else config.credit_monitoring.retry_delay_minutes
        ),
        1,
    )
    db = db_factory()
    try:
        for raw in results:
            if not isinstance(raw, CreditFetchResult):
                continue
            result: CreditFetchResult = raw
            try:
                key = db.query(ApiKey).filter(ApiKey.id == int(result.api_key_id)).one_or_none()
                if key is None:
                    continue

                # Drop terminal keys from future refresh if needed
                status = (key.status or "").lower()
                if result.kind == "decrypt_failed" or result.kind == "invalid":
                    apply_credit_fetch_result(db=db, key=key, result=result, config=config)
                    db.commit()
                    continue

                if (not key.is_active) or status in DISABLED_STATUSES:
                    if key.next_refresh_at is not None:
                        key.next_refresh_at = None
                        db.commit()
                    continue

                apply_credit_fetch_result(db=db, key=key, result=result, config=config)

                status_after = (key.status or "").lower()
                if (not key.is_active) or status_after in DISABLED_STATUSES:
                    key.next_refresh_at = None
                elif result.kind == "success":
                    # apply_credit_fetch_result already set next_refresh_at via calculate
                    pass
                else:
                    # probe error / cooling — back off, keep key eligible later
                    key.next_refresh_at = now + timedelta(minutes=retry_m)

                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "credit.refresh_apply_failed",
                    extra={"fields": {"api_key_id": getattr(result, "api_key_id", None)}},
                )
    finally:
        db.close()


async def _refresh_once(
    *,
    db_factory: Callable[[], Session],
    master_key: bytes,
    config: AppConfig,
    runtime_settings: object | None = None,
) -> None:
    db = db_factory()
    try:
        now = datetime.now(timezone.utc)

        # Due keys: load id + ciphertext for concurrent probe without holding the session.
        rows = (
            db.query(ApiKey.id, ApiKey.api_key_ciphertext, ApiKey.is_active, ApiKey.status)
            .filter(
                ApiKey.is_active.is_(True),
                ApiKey.status.notin_(list(DISABLED_STATUSES)),
                or_(ApiKey.next_refresh_at.is_(None), ApiKey.next_refresh_at <= now),
            )
            .order_by(ApiKey.id.asc())
            .all()
        )
        due: list[tuple[int, bytes]] = []
        clear_refresh_ids: list[int] = []
        for r in rows:
            kid = int(r[0])
            status = (r[3] or "").lower()
            if (not bool(r[2])) or status in DISABLED_STATUSES:
                clear_refresh_ids.append(kid)
                continue
            due.append((kid, r[1]))

        if clear_refresh_ids:
            db.query(ApiKey).filter(ApiKey.id.in_(clear_refresh_ids)).update(
                {ApiKey.next_refresh_at: None},
                synchronize_session=False,
            )
            try:
                db.commit()
            except Exception:
                db.rollback()

        if not due:
            return

        workers = max(int(getattr(config.credit_monitoring, "workers", 4) or 4), 1)
        batch_size = max(int(config.credit_monitoring.batch_size), 1)
        batch_delay = max(int(config.credit_monitoring.batch_delay_seconds), 0)
        retry_delay_minutes = max(int(config.credit_monitoring.retry_delay_minutes), 1)
        if runtime_settings is not None:
            for attr, var, floor in (
                ("effective_credit_workers", "workers", 1),
                ("effective_credit_batch_size", "batch_size", 1),
                ("effective_credit_batch_delay_seconds", "batch_delay", 0),
                ("effective_credit_retry_delay_minutes", "retry_delay_minutes", 1),
            ):
                fn = getattr(runtime_settings, attr, None)
                if not callable(fn):
                    continue
                try:
                    if var == "workers":
                        workers = max(int(fn(workers)), floor)
                    elif var == "batch_size":
                        batch_size = max(int(fn(batch_size)), floor)
                    elif var == "batch_delay":
                        batch_delay = max(int(fn(batch_delay)), floor)
                    else:
                        retry_delay_minutes = max(int(fn(retry_delay_minutes)), floor)
                except Exception:
                    pass
        # NOTE: SQLite no longer forces workers=1.
        # HTTP probes run concurrent; DB applies are serialized in _apply_results_serial.

        sem = asyncio.Semaphore(workers)

        logger.info(
            "credit.refresh_round",
            extra={
                "fields": {
                    "due": len(due),
                    "workers": workers,
                    "batch_size": batch_size,
                    "batch_delay_seconds": batch_delay,
                }
            },
        )
    finally:
        db.close()

    for i in range(0, len(due), batch_size):
        batch = due[i : i + batch_size]
        results = await asyncio.gather(
            *[
                _probe_one_key(
                    key_id=kid,
                    ciphertext=blob,
                    master_key=master_key,
                    config=config,
                    now=now,
                    sem=sem,
                )
                for kid, blob in batch
            ]
        )
        # Serial DB writer for this batch (memory → SQLite/Postgres)
        await asyncio.to_thread(
            _apply_results_serial,
            db_factory=db_factory,
            config=config,
            results=list(results),
            now=now,
            retry_delay_minutes=retry_delay_minutes,
        )
        if batch_delay and i + batch_size < len(due):
            await asyncio.sleep(batch_delay)

    db2 = db_factory()
    try:
        await cleanup_old_snapshots(db=db2, config=config)
    finally:
        db2.close()


async def cleanup_old_snapshots(*, db: Session, config: AppConfig) -> None:
    retention_days = int(config.credit_monitoring.retention_days)
    if retention_days <= 0:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    try:
        deleted = db.query(CreditSnapshot).filter(CreditSnapshot.snapshot_at < cutoff).delete()
        if deleted:
            db.commit()
            logger.info("credit.snapshots_cleaned", extra={"fields": {"deleted_count": int(deleted)}})
    except Exception:
        db.rollback()
        logger.exception("credit.snapshots_cleanup_failed")
