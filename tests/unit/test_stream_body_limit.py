from __future__ import annotations

import pytest
from starlette.requests import Request

from app.errors import FcamError
from app.middleware import RequestLimitsMiddleware

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_stream_body_limit_rejects_oversize_without_content_length():
    mw = RequestLimitsMiddleware(
        app=None,  # type: ignore[arg-type]
        max_body_bytes=16,
        allowed_api_paths={"scrape"},
        stream_body_limit=True,
    )

    async def app_receive():
        # first chunk ok, second pushes over limit
        yield {"type": "http.request", "body": b"0123456789", "more_body": True}
        yield {"type": "http.request", "body": b"0123456789ABCDEF", "more_body": False}

    chunks = [b"0123456789", b"0123456789ABCDEF"]
    idx = {"i": 0}

    async def receive():
        i = idx["i"]
        idx["i"] += 1
        if i < len(chunks):
            return {"type": "http.request", "body": chunks[i], "more_body": i < len(chunks) - 1}
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v2/scrape",
        "raw_path": b"/v2/scrape",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
        "scheme": "http",
    }
    request = Request(scope, receive)
    with pytest.raises(FcamError) as e:
        await mw._read_body_limited(request)
    assert e.value.code == "REQUEST_TOO_LARGE"
