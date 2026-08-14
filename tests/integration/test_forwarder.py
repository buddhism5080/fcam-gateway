from __future__ import annotations

import gzip
from typing import Any

import httpx
import pytest

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client
from app.db.session import create_engine_from_config, create_session_factory
from app.errors import FcamError

pytestmark = pytest.mark.integration


def _setup_db(tmp_path) -> tuple[AppConfig, Any]:
    config = AppConfig()
    config.database.path = (tmp_path / "fwd.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    SessionLocal = create_session_factory(engine)
    return config, SessionLocal()


def _add_client(db, master_key: str, *, max_retries: int = 3) -> tuple[Client, str]:
    token = "fcam_client_token"
    token_hash = hmac_sha256_hex(derive_master_key_bytes(master_key), token)
    c = Client(
        name="svc",
        token_hash=token_hash,
        is_active=True,
        daily_quota=None,
        daily_usage=0,
        max_retries=max_retries,
        rate_limit_per_min=10_000,
        max_concurrent=10,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c, token


def _add_key(
    db,
    master_key: str,
    api_key_plain: str,
    api_key_hash: str,
    last4: str,
    *,
    remaining: int | None = 1000,
) -> ApiKey:
    key_bytes = derive_master_key_bytes(master_key)
    cipher = encrypt_api_key(key_bytes, api_key_plain)
    k = ApiKey(
        client_id=None,
        api_key_ciphertext=cipher,
        api_key_hash=api_key_hash,
        api_key_last4=last4,
        is_active=True,
        status="active",
        daily_quota=0,
        daily_usage=0,
        quota_reset_at=today_in_timezone("UTC"),
        max_concurrent=10,
        rate_limit_per_min=10_000,
        cached_remaining_credits=remaining,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    return k


def test_forwarder_retries_on_429_and_switches_key(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=3)
    # Prefer k1 first via higher remaining credits
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    k2 = _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate"})
        if auth == "Bearer fc-key-2":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500, json={"error": "unexpected"})

    transport = httpx.MockTransport(handler)
    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=transport,
    )

    result = fwd.forward(
        db=db,
        request_id="req_12345678",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )

    assert result.upstream_status_code == 200
    assert result.api_key_id == k2.id
    assert result.retry_count >= 1

    db.refresh(k1)
    assert k1.status == "cooling"
    assert k1.cooldown_until is not None

    db.refresh(k2)
    assert k2.total_requests == 1


def test_forwarder_disables_key_on_401_then_uses_next(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    k2 = _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"ok": True})

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )

    result = fwd.forward(
        db=db,
        request_id="req_12345678",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )
    assert result.upstream_status_code == 200
    assert result.api_key_id == k2.id

    db.refresh(k1)
    assert k1.status == "invalid"
    assert k1.is_active is False
    assert k1.next_refresh_at is None


def test_forwarder_timeout_raises_gateway_error(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    # Non-key transport errors must not consume the retry budget.
    client, _ = _add_client(db, secrets.master_key, max_retries=3)
    _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001")
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        raise httpx.ReadTimeout("timeout", request=request)

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(FcamError) as e:
        fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )
    assert e.value.code == "UPSTREAM_TIMEOUT"
    assert e.value.status_code == 504
    assert hits["n"] == 1


def test_forwarder_marks_failed_after_threshold(tmp_path):
    config, db = _setup_db(tmp_path)
    config.firecrawl.failure_threshold = 1
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=0)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )

    result = fwd.forward(
        db=db,
        request_id="req_12345678",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )
    assert result.upstream_status_code == 500

    db.refresh(k1)
    assert k1.status == "failed"
    assert k1.cooldown_until is not None


def test_forwarder_disables_key_if_decrypt_fails_and_uses_next(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key)

    k_bad = ApiKey(
        client_id=None,
        api_key_ciphertext=b"not-a-valid-aesgcm-blob",
        api_key_hash="hbad",
        api_key_last4="0001",
        is_active=True,
        status="active",
        daily_quota=0,
        daily_usage=0,
        quota_reset_at=today_in_timezone("UTC"),
        max_concurrent=10,
        cached_remaining_credits=50_000,
    )
    db.add(k_bad)
    db.commit()
    db.refresh(k_bad)

    k_good = _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-2":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500, json={"error": "unexpected"})

    transport = httpx.MockTransport(handler)
    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=transport,
    )

    result = fwd.forward(
        db=db,
        request_id="req_12345678",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )

    assert result.upstream_status_code == 200
    assert result.api_key_id == k_good.id

    db.refresh(k_bad)
    assert k_bad.status == "decrypt_failed"
    assert k_bad.is_active is False


def test_forwarder_drops_content_encoding_and_length_headers(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key)
    _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001")

    raw = b'{"ok":true,"data":"hello"}' * 50
    gz = gzip.compress(raw)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(gz)),
            },
            content=gz,
        )

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )

    result = fwd.forward(
        db=db,
        request_id="req_12345678",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )

    resp = result.response
    assert resp.status_code == 200
    assert resp.body == raw
    assert resp.headers.get("content-encoding") is None
    assert int(resp.headers["content-length"]) == len(raw)


def test_forwarder_credit_error_switches_key(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=2)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    k2 = _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=200)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(402, json={"error": "Insufficient credits"})
        return httpx.Response(200, json={"ok": True})

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )
    result = fwd.forward(
        db=db,
        request_id="req_credit",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )
    assert result.upstream_status_code == 200
    assert result.api_key_id == k2.id
    db.refresh(k1)
    assert k1.cached_remaining_credits == 0


def test_forwarder_site_unsupported_403_does_not_disable_or_switch(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=3)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(
            403,
            json={
                "success": False,
                "error": "We apologize for the inconvenience but we do not support this site.",
            },
        )

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )
    result = fwd.forward(
        db=db,
        request_id="req_site_403",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://blocked.example"},
        inbound_headers={"content-type": "application/json"},
    )
    assert result.upstream_status_code == 403
    assert result.api_key_id == k1.id
    assert result.retry_count == 0
    assert hits["n"] == 1
    db.refresh(k1)
    assert k1.status == "active"
    assert k1.is_active is True
    assert k1.cached_remaining_credits == 50_000


def test_forwarder_scrape_engines_failed_500_does_not_switch_key(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=3)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(
            500,
            json={
                "success": False,
                "code": "SCRAPE_ALL_ENGINES_FAILED",
                "error": "All scraping engines failed to retrieve content from this URL.",
            },
        )

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )
    result = fwd.forward(
        db=db,
        request_id="req_site_500",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://blocked.example"},
        inbound_headers={"content-type": "application/json"},
    )
    # Site/request 5xx must not retry or switch keys.
    assert result.upstream_status_code == 500
    assert result.retry_count == 0
    assert hits["n"] == 1
    db.refresh(k1)
    assert k1.status == "active"
    assert k1.is_active is True
    assert k1.cached_remaining_credits == 50_000


def test_forwarder_generic_500_does_not_retry(tmp_path):
    config, db = _setup_db(tmp_path)
    secrets = Secrets(admin_token="admin", master_key="master")
    client, _ = _add_client(db, secrets.master_key, max_retries=3)
    k1 = _add_key(db, secrets.master_key, "fc-key-1", "h1", "0001", remaining=50_000)
    _add_key(db, secrets.master_key, "fc-key-2", "h2", "0002", remaining=100)
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(500, json={"error": "Internal Server Error"})

    fwd = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )
    result = fwd.forward(
        db=db,
        request_id="req_generic_500",
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body={"url": "https://example.com"},
        inbound_headers={"content-type": "application/json"},
    )
    assert result.upstream_status_code == 500
    assert result.retry_count == 0
    assert hits["n"] == 1
    db.refresh(k1)
    assert k1.status != "invalid"
    assert k1.is_active is True
    assert k1.cached_remaining_credits == 50_000
