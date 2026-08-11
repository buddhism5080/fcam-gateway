from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.responses import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import AppConfig
from app.core.idempotency import complete, start_or_replay
from app.core.urlutil import strip_provider_version_suffix
from app.db.models import Base, Client, IdempotencyRecord
from app.errors import FcamError

pytestmark = pytest.mark.unit


def test_strip_provider_version_suffix():
    base, ver = strip_provider_version_suffix("https://api.firecrawl.dev/v1")
    assert base == "https://api.firecrawl.dev"
    assert ver == "v1"

    base, ver = strip_provider_version_suffix("https://api.firecrawl.dev/v2/")
    assert base == "https://api.firecrawl.dev"
    assert ver == "v2"

    base, ver = strip_provider_version_suffix("https://api.firecrawl.dev")
    assert base == "https://api.firecrawl.dev"
    assert ver is None


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_idempotency_stale_in_progress_can_take_over():
    db = _session()
    config = AppConfig()
    config.idempotency.enabled = True
    config.idempotency.ttl_seconds = 3600

    c = Client(name="svc", token_hash="h", is_active=True, max_retries=1)
    db.add(c)
    db.commit()
    db.refresh(c)

    # First claim
    ctx, replay = start_or_replay(
        db=db,
        config=config,
        client_id=c.id,
        idempotency_key="k1",
        endpoint="crawl",
        method="POST",
        payload={"url": "https://a"},
    )
    assert ctx is not None and replay is None

    # Concurrent claim while in_progress → 409
    with pytest.raises(FcamError) as e:
        start_or_replay(
            db=db,
            config=config,
            client_id=c.id,
            idempotency_key="k1",
            endpoint="crawl",
            method="POST",
            payload={"url": "https://a"},
        )
    assert e.value.code == "IDEMPOTENCY_IN_PROGRESS"

    # Age the record past stale threshold
    rec = db.query(IdempotencyRecord).one()
    rec.created_at = datetime.now(timezone.utc) - timedelta(seconds=200)
    db.commit()

    ctx2, replay2 = start_or_replay(
        db=db,
        config=config,
        client_id=c.id,
        idempotency_key="k1",
        endpoint="crawl",
        method="POST",
        payload={"url": "https://a"},
    )
    assert ctx2 is not None and replay2 is None

    # Complete and replay
    complete(
        db=db,
        config=config,
        ctx=ctx2,
        response=Response(content=b'{"ok":true}', status_code=200, media_type="application/json"),
    )
    ctx3, replay3 = start_or_replay(
        db=db,
        config=config,
        client_id=c.id,
        idempotency_key="k1",
        endpoint="crawl",
        method="POST",
        payload={"url": "https://a"},
    )
    assert ctx3 is None and replay3 is not None
    assert replay3.status_code == 200
