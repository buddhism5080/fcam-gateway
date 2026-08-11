from __future__ import annotations

import httpx
import pytest

from app.core.http_pool import UpstreamHttpPool
from app.core.runtime_settings import RuntimeSettings
from app.config import SchedulingConfig

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
    # When transport is set, pool forces ephemeral for test isolation.
    pool = UpstreamHttpPool(enabled=True, transport=transport)
    with pool.acquire(base_url="https://example.com", timeout=httpx.Timeout(5)) as c:
        r = c.get("/x")
        assert r.status_code == 200

    # Without transport, reuse works
    pool2 = UpstreamHttpPool(enabled=True)
    t = httpx.Timeout(5.0)
    with pool2.acquire(base_url="https://example.com", timeout=t) as c1:
        id1 = id(c1)
    with pool2.acquire(base_url="https://example.com", timeout=t) as c2:
        id2 = id(c2)
    assert id1 == id2
    pool2.close()


def test_runtime_settings_hot_patch():
    rs = RuntimeSettings(scheduling=SchedulingConfig(freshness_half_life_seconds=100, unknown_credit_baseline=10))
    assert rs.effective_half_life(999) == 100
    rs.patch_scheduling(freshness_half_life_seconds=200, unknown_credit_baseline=3.5, credit_workers=8)
    assert rs.effective_half_life(1) == 200
    assert rs.effective_unknown_baseline(1) == 3.5
    assert rs.effective_credit_workers(4) == 8
    rs.patch_scheduling(unset_credit_workers=True)
    assert rs.effective_credit_workers(4) == 4
