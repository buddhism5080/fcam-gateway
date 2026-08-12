from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.credit_fetcher import CreditFetchResult, apply_credit_fetch_result
from app.core import credit_refresh as cr
from app.db.models import ApiKey, CreditSnapshot

pytestmark = pytest.mark.unit


class _MemDB:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add(self, obj) -> None:
        self.added.append(obj)
        if isinstance(obj, CreditSnapshot) and getattr(obj, "id", None) is None:
            obj.id = len(self.added)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


def _make_key(kid: int) -> ApiKey:
    return ApiKey(
        id=kid,
        api_key_ciphertext=b"\x00" * 28,
        api_key_hash=f"h{kid}",
        api_key_last4="abcd",
        is_active=True,
        status="active",
        provider="firecrawl",
    )


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        credit_monitoring=SimpleNamespace(
            smart_refresh=SimpleNamespace(
                enabled=True,
                high_usage_interval=30,
                medium_usage_interval=60,
                normal_usage_interval=120,
                low_usage_interval=240,
            ),
            fixed_refresh=SimpleNamespace(interval_minutes=60),
            workers=4,
            batch_size=10,
            batch_delay_seconds=0,
            retry_delay_minutes=10,
            retention_days=0,
        ),
        firecrawl=SimpleNamespace(base_url="https://api.firecrawl.dev", timeout=5),
    )


def test_apply_success_sets_cache():
    key = _make_key(7)
    db = _MemDB()
    result = CreditFetchResult(
        api_key_id=7,
        kind="success",
        remaining_credits=42,
        plan_credits=100,
        fetched_at=datetime.now(timezone.utc),
    )
    snap = apply_credit_fetch_result(db=db, key=key, result=result, config=_cfg())  # type: ignore[arg-type]
    assert snap.fetch_success is True
    assert key.cached_remaining_credits == 42
    assert key.cached_plan_credits == 100
    assert key.last_credit_check_at is not None
    assert key.next_refresh_at is not None


def test_apply_invalid_disables():
    key = _make_key(3)
    db = _MemDB()
    result = CreditFetchResult(
        api_key_id=3,
        kind="invalid",
        error_message="401",
        http_status=401,
        fetched_at=datetime.now(timezone.utc),
    )
    apply_credit_fetch_result(db=db, key=key, result=result, config=_cfg())  # type: ignore[arg-type]
    assert key.status == "invalid"
    assert key.is_active is False
    assert key.next_refresh_at is None


@pytest.mark.asyncio
async def test_probe_batch_respects_worker_concurrency(monkeypatch):
    """Workers>1 should allow concurrent probes even when DB is SQLite-like."""
    inflight = 0
    max_inflight = 0

    async def fake_probe(*, api_key_id, plaintext_api_key, config, request_id):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return CreditFetchResult(
            api_key_id=api_key_id,
            kind="success",
            remaining_credits=1,
            plan_credits=10,
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("app.core.credit_fetcher.probe_credit_from_firecrawl", fake_probe)
    monkeypatch.setattr(cr, "decrypt_api_key", lambda mk, blob: "fc-test-key-xxxxxxxxxxxx")

    sem = asyncio.Semaphore(4)
    now = datetime.now(timezone.utc)
    results = await asyncio.gather(
        *[
            cr._probe_one_key(
                key_id=i,
                ciphertext=b"\x00" * 28,
                master_key=b"\x11" * 32,
                config=_cfg(),  # type: ignore[arg-type]
                now=now,
                sem=sem,
            )
            for i in range(1, 5)
        ]
    )
    assert len(results) == 4
    assert all(getattr(r, "kind", None) == "success" for r in results)
    assert max_inflight >= 2


def test_sqlite_no_longer_forces_workers_one_in_source():
    import inspect

    src = inspect.getsource(cr._refresh_once)
    assert "workers = 1" not in src
    assert "dialect.name == \"sqlite\"" not in src
