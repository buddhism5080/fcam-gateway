from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.key_pool import (
    KeyPool,
    NETWORK_FAILURE_REASONS,
    is_4xx_score_failure,
    score_key,
)
from app.core.rate_limit import TokenBucketRateLimiter
from app.db.models import ApiKey

pytestmark = pytest.mark.unit


def _key(**kwargs) -> ApiKey:
    defaults = dict(
        id=1,
        api_key_ciphertext=b"\x00" * 28,
        api_key_hash="h",
        api_key_last4="abcd",
        is_active=True,
        status="active",
        provider="firecrawl",
        rate_limit_per_min=60,
        max_concurrent=5,
        cached_remaining_credits=100,
        cached_plan_credits=100,
        last_credit_check_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return ApiKey(**defaults)


def test_unknown_baseline_is_credits_via_log1p():
    """Config 50 means 50 credits → weight log1p(50), not raw 50."""
    now = datetime.now(timezone.utc)
    unknown = _key(cached_remaining_credits=None, last_credit_check_at=None)
    known50 = _key(cached_remaining_credits=50, last_credit_check_at=None)
    s_u = score_key(unknown, now, unknown_credit_baseline=50.0)
    s_k = score_key(known50, now, unknown_credit_baseline=50.0)
    assert s_u is not None and s_k is not None
    assert abs(s_u - s_k) < 1e-9
    assert abs(s_u - math.log1p(50) * 0.55) < 1e-9
    assert s_u < 5


def test_only_4xx_reduces_score_not_5xx_or_network():
    pool = KeyPool()
    now = datetime.now(timezone.utc)
    k = _key(id=7, cached_remaining_credits=1000)
    base = score_key(k, now)
    assert base is not None

    pool.record_failure(7, "timeout")
    assert pool._failure_count(7, half_life_seconds=300) == 0

    pool.record_failure(7, "upstream_5xx", status_code=502)
    assert pool._failure_count(7, half_life_seconds=300) == 0

    pool.record_failure(7, "key_4xx_429", status_code=429)
    pool.record_failure(7, "429", status_code=429)
    fails = pool._failure_count(7, half_life_seconds=300)
    assert fails >= 1.9
    penalized = score_key(k, now, non_network_failure_count=fails, failure_penalty_unit=1.0)
    assert penalized is not None
    assert penalized < base

    pool.record_success(7)
    assert pool._failure_count(7, half_life_seconds=300) == 0


def test_is_4xx_score_failure_helpers():
    assert is_4xx_score_failure(status_code=429) is True
    assert is_4xx_score_failure(status_code=401) is True
    assert is_4xx_score_failure(status_code=402) is True
    assert is_4xx_score_failure(status_code=403) is False
    assert is_4xx_score_failure(status_code=400) is False
    assert is_4xx_score_failure(status_code=422) is False
    assert is_4xx_score_failure(status_code=500) is False
    assert is_4xx_score_failure(status_code=200) is False
    assert is_4xx_score_failure(reason="timeout") is False
    assert is_4xx_score_failure(reason="upstream_5xx") is False
    assert is_4xx_score_failure(reason="upstream_4xx_400") is False
    assert is_4xx_score_failure(reason="key_4xx_429") is True
    assert is_4xx_score_failure(reason="429") is True


def test_rpm_peek_filters_without_consuming():
    lim = TokenBucketRateLimiter()
    for _ in range(60):
        ok, _ = lim.allow("1", 60)
        assert ok
    ok, _ = lim.allow("1", 60)
    assert not ok
    peek_ok, _ = lim.peek("1", 60)
    assert peek_ok is False
    peek_ok2, _ = lim.peek("1", 60)
    assert peek_ok2 is False

    pool = KeyPool(rate_limiter=lim)
    k = _key(id=1, rate_limit_per_min=60)
    assert pool._rpm_allows(k) is False
    k2 = _key(id=2, rate_limit_per_min=60)
    assert pool._rpm_allows(k2) is True


def test_epsilon_explore_uses_rotation(monkeypatch):
    pool = KeyPool()
    cfg = SimpleNamespace(
        scheduling=SimpleNamespace(
            freshness_half_life_seconds=21600,
            unknown_credit_baseline=50,
            epsilon_greedy=1.0,
            near_score_ratio=0.95,
            failure_penalty_half_life_seconds=300,
            failure_penalty_unit=1.0,
        )
    )
    keys = [
        _key(id=i, cached_remaining_credits=10_000 - i * 100, api_key_hash=f"h{i}")
        for i in range(1, 5)
    ]
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = keys

    seen = []
    for _ in range(4):
        sel = pool.select(db, cfg, provider="firecrawl")  # type: ignore[arg-type]
        assert sel.explore is True
        seen.append(sel.api_key.id)
    assert seen == [1, 2, 3, 4]


def test_network_failure_reasons_include_5xx():
    assert "timeout" in NETWORK_FAILURE_REASONS
    assert "upstream_5xx" in NETWORK_FAILURE_REASONS
