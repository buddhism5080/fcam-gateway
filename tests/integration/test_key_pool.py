from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import AppConfig
from app.core.key_pool import KeyPool, score_key
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base
from app.db.session import create_engine_from_config, create_session_factory
from app.errors import FcamError

pytestmark = pytest.mark.integration


def _db(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "keys.db").as_posix()
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    SessionLocal = create_session_factory(engine)
    return config, SessionLocal()


def _key(**kwargs):
    defaults = dict(
        api_key_ciphertext=b"x",
        is_active=True,
        status="active",
        daily_quota=0,
        daily_usage=0,
        max_concurrent=1,
        rate_limit_per_min=60,
        provider="firecrawl",
    )
    defaults.update(kwargs)
    return ApiKey(**defaults)


def test_key_pool_select_skips_disabled_and_cooling(tmp_path):
    config, db = _db(tmp_path)

    db.add(_key(api_key_hash="h1", api_key_last4="1111", is_active=False, status="disabled"))
    db.add(
        _key(
            api_key_hash="h2",
            api_key_last4="2222",
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=3600),
        )
    )
    db.add(_key(api_key_hash="h3", api_key_last4="3333", cached_remaining_credits=100))
    db.commit()

    pool = KeyPool()
    selected = pool.select(db, config)
    assert selected.api_key.api_key_hash == "h3"


def test_key_pool_select_no_keys(tmp_path):
    config, db = _db(tmp_path)
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "NO_KEY_CONFIGURED"


def test_key_pool_select_all_disabled(tmp_path):
    config, db = _db(tmp_path)
    db.add(_key(api_key_hash="h1", api_key_last4="1111", is_active=False, status="disabled"))
    db.commit()
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "ALL_KEYS_DISABLED"


def test_key_pool_select_zero_credits_never_selected(tmp_path):
    config, db = _db(tmp_path)
    db.add(_key(api_key_hash="h1", api_key_last4="1111", cached_remaining_credits=0))
    db.commit()
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "ALL_KEYS_NO_CREDITS"


def test_key_pool_prefers_high_credit_fresh(tmp_path):
    config, db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    # Low credit, stale
    db.add(
        _key(
            api_key_hash="low",
            api_key_last4="0001",
            cached_remaining_credits=10,
            last_credit_check_at=now - timedelta(hours=48),
        )
    )
    # High credit, fresh
    db.add(
        _key(
            api_key_hash="high",
            api_key_last4="0002",
            cached_remaining_credits=5000,
            last_credit_check_at=now,
        )
    )
    db.commit()

    pool = KeyPool()
    # Weighted random — over many draws, high should dominate
    counts = {"high": 0, "low": 0}
    for _ in range(40):
        s = pool.select(db, config)
        counts[s.api_key.api_key_hash] += 1
    assert counts["high"] > counts["low"]


def test_key_pool_global_ignores_client_id(tmp_path):
    """Keys are shared globally; client_id filter is ignored."""
    config, db = _db(tmp_path)
    db.add(_key(api_key_hash="g1", api_key_last4="9999", client_id=None, cached_remaining_credits=100))
    db.commit()
    pool = KeyPool()
    # Even with a non-existent client_id, global pool still selects
    selected = pool.select(db, config, client_id=999)
    assert selected.api_key.api_key_hash == "g1"


def test_key_pool_select_all_cooling(tmp_path):
    config, db = _db(tmp_path)
    db.add(
        _key(
            api_key_hash="h1",
            api_key_last4="1111",
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=60),
            cached_remaining_credits=100,
        )
    )
    db.add(
        _key(
            api_key_hash="h2",
            api_key_last4="2222",
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=120),
            cached_remaining_credits=100,
        )
    )
    db.commit()

    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)

    assert e.value.code == "ALL_KEYS_COOLING"
    assert e.value.status_code == 429
    assert int(e.value.retry_after or 0) >= 1


def test_score_key_zero_ineligible():
    k = ApiKey(
        api_key_ciphertext=b"x",
        api_key_hash="h",
        api_key_last4="0000",
        cached_remaining_credits=0,
    )
    assert score_key(k, datetime.now(timezone.utc)) is None


def test_score_key_fresh_beats_stale():
    now = datetime.now(timezone.utc)
    fresh = ApiKey(
        api_key_ciphertext=b"x",
        api_key_hash="f",
        api_key_last4="0001",
        cached_remaining_credits=100,
        last_credit_check_at=now,
    )
    stale = ApiKey(
        api_key_ciphertext=b"x",
        api_key_hash="s",
        api_key_last4="0002",
        cached_remaining_credits=100,
        last_credit_check_at=now - timedelta(hours=48),
    )
    assert score_key(fresh, now) > score_key(stale, now)
