from __future__ import annotations

import httpx
import pytest

from app.core.forwarder import (
    _looks_like_invalid_key,
    _looks_like_site_or_request_error,
    _parse_retry_after,
    _strip_firecrawl_version_suffix,
    classify_upstream_outcome,
)

pytestmark = pytest.mark.unit


def test_strip_firecrawl_version_suffix_handles_invalid_url_and_v2_suffix():
    base, version = _strip_firecrawl_version_suffix("firecrawl.test/v1")
    assert base == "firecrawl.test/v1"
    assert version is None

    base2, version2 = _strip_firecrawl_version_suffix("http://firecrawl.test/v2/")
    assert base2 == "http://firecrawl.test"
    assert version2 == "v2"


def test_parse_retry_after_handles_missing_invalid_and_negative():
    assert _parse_retry_after(httpx.Headers({})) is None
    assert _parse_retry_after(httpx.Headers({"retry-after": "abc"})) is None
    assert _parse_retry_after(httpx.Headers({"retry-after": "-5"})) == 0


def _resp(status: int, payload: dict | None = None, text: str | None = None) -> httpx.Response:
    if payload is not None:
        return httpx.Response(status, json=payload)
    return httpx.Response(status, text=text or "")


def test_site_unsupported_403_is_not_key_fault():
    resp = _resp(
        403,
        {
            "success": False,
            "error": (
                "We apologize for the inconvenience but we do not support this site. "
                "If you are part of an enterprise and want to have a further conversation "
                "about this, please fill out our intake form here: "
                "https://fk4bvu0n5qp.typeform.com/to/Ej6oydlg"
            ),
        },
    )
    assert _looks_like_site_or_request_error(resp) is True
    assert _looks_like_invalid_key(resp) is False
    assert classify_upstream_outcome(resp) == "passthrough"


def test_scrape_all_engines_failed_500_is_not_key_fault():
    resp = _resp(
        500,
        {
            "success": False,
            "code": "SCRAPE_ALL_ENGINES_FAILED",
            "error": (
                "All scraping engines failed to retrieve content from this URL. "
                "This usually happens when: (1) The URL is invalid or the page "
                "doesn't exist (404), (2) The website is blocking automated access."
            ),
        },
    )
    assert _looks_like_site_or_request_error(resp) is True
    assert classify_upstream_outcome(resp) == "passthrough"


def test_generic_400_404_422_are_request_or_site_not_key():
    for status, payload in (
        (400, {"success": False, "error": "Invalid URL"}),
        (404, {"success": False, "error": "Not Found"}),
        (422, {"success": False, "error": "Unprocessable Entity"}),
    ):
        resp = _resp(status, payload)
        assert classify_upstream_outcome(resp) == "passthrough"
        assert _looks_like_invalid_key(resp) is False


def test_401_invalid_token_is_key_fault():
    resp = _resp(401, {"success": False, "error": "Unauthorized: Invalid token"})
    assert _looks_like_invalid_key(resp) is True
    assert classify_upstream_outcome(resp) == "invalid_key"


def test_402_and_credit_body_are_credit_fault():
    assert classify_upstream_outcome(_resp(402, {"error": "Payment Required: Insufficient credits"})) == "credit"
    assert classify_upstream_outcome(_resp(400, {"error": "out of credits"})) == "credit"


def test_429_rate_limit_is_key_rate_limit():
    assert classify_upstream_outcome(_resp(429, {"error": "Rate limit exceeded"})) == "rate_limit"


def test_bare_403_without_site_markers_is_not_auto_invalid():
    """Official 403 can be plan/scope — do not permanently disable without auth wording."""
    resp = _resp(403, {"success": False, "error": "Forbidden"})
    assert _looks_like_invalid_key(resp) is False
    assert classify_upstream_outcome(resp) == "passthrough"


def test_403_with_invalid_token_wording_is_key_fault():
    resp = _resp(403, {"success": False, "error": "Unauthorized: Invalid token"})
    assert _looks_like_invalid_key(resp) is True
    assert classify_upstream_outcome(resp) == "invalid_key"

