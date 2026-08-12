from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import SchedulingConfig
from app.core.http_pool import UpstreamHttpPool
from app.core.runtime_settings import RuntimeSettings, default_runtime_settings_path

pytestmark = pytest.mark.unit


def test_http_pool_disabled_is_ephemeral():
    pool = UpstreamHttpPool(enabled=False)
    assert pool.enabled is False
    t1 = httpx.Timeout(5.0)
    with pool.acquire(base_url="https://example.com", timeout=t1) as c1:
        id1 = id(c1)
    with pool.acquire(base_url="https://example.com", timeout=t1) as c2:
        id2 = id(c2)
    assert id1 != id2


def test_http_pool_enabled_reuses_client():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    pool = UpstreamHttpPool(enabled=True, transport=transport)
    with pool.acquire(base_url="https://example.com", timeout=httpx.Timeout(5)) as c:
        r = c.get("/x")
        assert r.status_code == 200

    pool2 = UpstreamHttpPool(enabled=True)
    t = httpx.Timeout(5.0)
    with pool2.acquire(base_url="https://example.com", timeout=t) as c1:
        id1 = id(c1)
    with pool2.acquire(base_url="https://example.com", timeout=t) as c2:
        id2 = id(c2)
    assert id1 == id2
    pool2.close()


def test_http_pool_set_enabled_hot_toggle():
    pool = UpstreamHttpPool(enabled=False)
    assert pool.enabled is False
    pool.set_enabled(True)
    assert pool.enabled is True
    t = httpx.Timeout(5.0)
    with pool.acquire(base_url="https://example.com", timeout=t) as c1:
        id1 = id(c1)
    with pool.acquire(base_url="https://example.com", timeout=t) as c2:
        id2 = id(c2)
    assert id1 == id2
    pool.set_enabled(False)
    assert pool.enabled is False
    with pool.acquire(base_url="https://example.com", timeout=t) as c3:
        id3 = id(c3)
    with pool.acquire(base_url="https://example.com", timeout=t) as c4:
        id4 = id(c4)
    assert id3 != id4


def test_runtime_settings_hot_patch():
    rs = RuntimeSettings(
        scheduling=SchedulingConfig(freshness_half_life_seconds=100, unknown_credit_baseline=10),
        persist_path=None,
        load_persisted=False,
    )
    assert rs.effective_half_life(999) == 100
    rs.patch_scheduling(
        freshness_half_life_seconds=200,
        unknown_credit_baseline=3.5,
        credit_workers=8,
        credit_batch_delay_seconds=5,
        credit_refresh_check_interval_seconds=300,
        persist=False,
    )
    assert rs.effective_half_life(1) == 200
    assert rs.effective_unknown_baseline(1) == 3.5
    assert rs.effective_credit_workers(4) == 8
    assert rs.effective_credit_batch_delay_seconds(99) == 5
    assert rs.effective_credit_refresh_check_interval_seconds(1) == 300
    rs.patch_scheduling(unset_credit_workers=True, persist=False)
    assert rs.effective_credit_workers(4) == 4
    assert rs.effective_http_connection_pool_enabled(False) is False
    rs.patch_scheduling(http_connection_pool_enabled=True, persist=False)
    assert rs.effective_http_connection_pool_enabled(False) is True
    rs.patch_scheduling(clear_http_connection_pool_override=True, persist=False)
    assert rs.effective_http_connection_pool_enabled(False) is False


def test_runtime_settings_persist_roundtrip(tmp_path: Path):
    path = tmp_path / "runtime_settings.json"
    rs = RuntimeSettings(
        scheduling=SchedulingConfig(freshness_half_life_seconds=100, unknown_credit_baseline=10),
        persist_path=path,
        load_persisted=False,
    )
    rs.patch_scheduling(
        credit_workers=7,
        credit_batch_delay_seconds=12,
        credit_refresh_check_interval_seconds=90,
        http_connection_pool_enabled=True,
        persist=True,
    )
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["overrides"]["credit_workers"] == 7
    assert raw["overrides"]["credit_batch_delay_seconds"] == 12
    assert raw["overrides"]["credit_refresh_check_interval_seconds"] == 90
    assert raw["overrides"]["http_connection_pool_enabled"] is True

    rs2 = RuntimeSettings(
        scheduling=SchedulingConfig(freshness_half_life_seconds=100, unknown_credit_baseline=10),
        persist_path=path,
        load_persisted=True,
    )
    assert rs2.effective_credit_workers(4) == 7
    assert rs2.effective_credit_batch_delay_seconds(5) == 12
    assert rs2.effective_credit_refresh_check_interval_seconds(300) == 90
    assert rs2.effective_http_connection_pool_enabled(False) is True


def test_default_runtime_settings_path_near_db(tmp_path: Path):
    db = tmp_path / "data" / "api_manager.db"
    db.parent.mkdir(parents=True)
    p = default_runtime_settings_path(database_path=str(db))
    assert p == db.parent / "runtime_settings.json"
