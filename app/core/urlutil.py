from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def strip_provider_version_suffix(base_url: str) -> tuple[str, str | None]:
    """
    Strip a trailing /v1 or /v2 path segment from an upstream base URL.

    Returns (normalized_base_url_without_version, version_or_None).
    """
    normalized = (base_url or "").rstrip("/")
    parts = urlsplit(normalized)
    if not parts.scheme or not parts.netloc:
        return normalized, None

    path = parts.path.rstrip("/")
    version: str | None = None
    if path.endswith("/v1"):
        version = "v1"
        path = path[:-3]
    elif path.endswith("/v2"):
        version = "v2"
        path = path[:-3]

    stripped = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return stripped.rstrip("/"), version
