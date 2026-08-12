from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.credit_fetcher import (
    CreditFetchResult,
    refresh_credits_after_4xx,
    _4xx_refresh_last_mono,
)
from app.db.models import ApiKey

pytestmark = pytest.mark.unit


def _key(**kwargs) -> ApiKey:
    defaults = dict(
        id=42,
        api_key_ciphertext=b"\x00" * 28,
        api_key_hash="h42",
        api_key_last4="4242",
        is_active=True,
        status="active",
        provider="firecrawl",
        cached_remaining_credits=100,
        cached_plan_credits=200,
        last_credit_check_at=None,
    )
    defaults.update(kwargs)
    return ApiKey(**defaults)


def test_refresh_credits_after_4xx_applies_success_and_updates_score_inputs():
    _4xx_refresh_last_mono.clear()
    key = _key()
    db = MagicMock()
    cfg = SimpleNamespace(
        firecrawl=SimpleNamespace(timeout=5, base_url="https://api.firecrawl.dev"),
        credit_monitoring=SimpleNamespace(
            smart_refresh=SimpleNamespace(
                enabled=True,
                high_usage_interval=30,
                medium_usage_interval=60,
                normal_usage_interval=120,
                low_usage_interval=240,
                abundant_remaining_threshold=1000,
                abundant_min_interval_minutes=120,
            ),
            fixed_refresh=SimpleNamespace(interval_minutes=60),
        ),
    )
    # minimal AppConfig-like: apply_credit_fetch_result needs calculate_next_refresh
    from app.config import AppConfig

    real_cfg = AppConfig()
    result = CreditFetchResult(
        api_key_id=42,
        kind="success",
        remaining_credits=777,
        plan_credits=1000,
        fetched_at=datetime.now(timezone.utc),
    )
    with patch("app.core.credit_fetcher.decrypt_api_key", return_value="fc-test"), patch(
        "app.core.credit_fetcher.probe_credit_from_firecrawl_sync", return_value=result
    ):
        out = refresh_credits_after_4xx(
            db=db,
            key=key,
            master_key=b"\x01" * 32,
            config=real_cfg,
            status_code=429,
            debounce_seconds=0,
        )
    assert out is not None
    assert out.kind == "success"
    assert key.cached_remaining_credits == 777
    assert key.last_credit_check_at is not None
    db.commit.assert_called()

    # score uses new remaining
    from app.core.key_pool import score_key

    s = score_key(key, datetime.now(timezone.utc))
    assert s is not None
    import math

    assert abs(s - math.log1p(777) * 1.0) < 0.05 or s > math.log1p(100)  # fresher + higher remaining


def test_refresh_credits_after_4xx_debounced():
    _4xx_refresh_last_mono.clear()
    key = _key(id=99)
    db = MagicMock()
    from app.config import AppConfig

    real_cfg = AppConfig()
    result = CreditFetchResult(
        api_key_id=99,
        kind="success",
        remaining_credits=1,
        plan_credits=10,
        fetched_at=datetime.now(timezone.utc),
    )
    with patch("app.core.credit_fetcher.decrypt_api_key", return_value="fc-test"), patch(
        "app.core.credit_fetcher.probe_credit_from_firecrawl_sync", return_value=result
    ) as probe:
        a = refresh_credits_after_4xx(
            db=db, key=key, master_key=b"\x01" * 32, config=real_cfg, status_code=400, debounce_seconds=60
        )
        b = refresh_credits_after_4xx(
            db=db, key=key, master_key=b"\x01" * 32, config=real_cfg, status_code=400, debounce_seconds=60
        )
    assert a is not None
    assert b is None
    assert probe.call_count == 1
