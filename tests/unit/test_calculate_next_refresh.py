from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.credit_refresh import calculate_next_refresh_time
from app.core.key_pool import DISABLED_STATUSES
from app.db.models import ApiKey

pytestmark = pytest.mark.unit


def _cfg(
    *,
    high=30,
    medium=60,
    normal=120,
    low=240,
    abundant_threshold=1000,
    abundant_floor=120,
    smart=True,
    fixed=60,
):
    return SimpleNamespace(
        credit_monitoring=SimpleNamespace(
            smart_refresh=SimpleNamespace(
                enabled=smart,
                high_usage_interval=high,
                medium_usage_interval=medium,
                normal_usage_interval=normal,
                low_usage_interval=low,
                abundant_remaining_threshold=abundant_threshold,
                abundant_min_interval_minutes=abundant_floor,
            ),
            fixed_refresh=SimpleNamespace(interval_minutes=fixed),
        )
    )


def _key(*, remaining, plan, status="active", active=True) -> ApiKey:
    return ApiKey(
        id=1,
        api_key_ciphertext=b"\x00" * 28,
        api_key_hash="h",
        api_key_last4="abcd",
        is_active=active,
        status=status,
        provider="firecrawl",
        cached_remaining_credits=remaining,
        cached_plan_credits=plan,
    )


def test_disabled_returns_none():
    for st in DISABLED_STATUSES:
        assert calculate_next_refresh_time(_key(remaining=10, plan=100, status=st, active=False), _cfg()) is None


def test_unknown_cache_is_now():
    before = datetime.now(timezone.utc)
    nxt = calculate_next_refresh_time(_key(remaining=None, plan=None), _cfg())
    assert nxt is not None
    assert abs((nxt - before).total_seconds()) < 2


def test_usage_tiers_scaled_30_to_240():
    # plan=1000 so ratios are easy
    cases = [
        (50, 30),  # usage 0.95 → high
        (200, 60),  # 0.80 → medium
        (400, 120),  # 0.60 → normal
        (800, 240),  # 0.20 → low
    ]
    for remaining, expect_min in cases:
        before = datetime.now(timezone.utc)
        nxt = calculate_next_refresh_time(_key(remaining=remaining, plan=1000), _cfg())
        assert nxt is not None
        delta = (nxt - before).total_seconds() / 60
        assert expect_min - 0.1 <= delta <= expect_min + 0.2


def test_abundant_remaining_floors_to_120():
    # High usage would want 30m, but remaining >= 1000 forces >= 120m
    before = datetime.now(timezone.utc)
    # plan=2000, remaining=1100 → usage=0.45 → low tier 240 anyway
    nxt_low = calculate_next_refresh_time(_key(remaining=1100, plan=2000), _cfg())
    assert nxt_low is not None
    assert (nxt_low - before).total_seconds() / 60 >= 120

    # plan=1050, remaining=1000 → usage ≈ 0.048 → low 240
    # Force high-usage path with huge plan so usage > 0.9 but remaining still >= 1000
    # remaining=1000, plan=20000 → usage=0.95 → high 30, floored to 120
    before2 = datetime.now(timezone.utc)
    nxt = calculate_next_refresh_time(_key(remaining=1000, plan=20000), _cfg())
    assert nxt is not None
    delta = (nxt - before2).total_seconds() / 60
    assert 119.9 <= delta <= 120.5


def test_remaining_below_1000_can_use_30():
    before = datetime.now(timezone.utc)
    # remaining=50, plan=1000 → high usage 30, no abundant floor
    nxt = calculate_next_refresh_time(_key(remaining=50, plan=1000), _cfg())
    assert nxt is not None
    delta = (nxt - before).total_seconds() / 60
    assert 29.9 <= delta <= 30.5


def test_zero_remaining_next_month():
    now = datetime.now(timezone.utc)
    nxt = calculate_next_refresh_time(_key(remaining=0, plan=500), _cfg())
    assert nxt is not None
    assert nxt.day == 1
    assert nxt > now
