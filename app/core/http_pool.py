from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

import httpx

logger = logging.getLogger(__name__)


class UpstreamHttpPool:
    """
    Optional shared httpx clients (connection reuse).

    Default: disabled → each acquire() opens a short-lived Client (legacy behaviour).
    Enabled: long-lived Client per base_url; request-level timeout still applies.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        keepalive_expiry: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._transport = transport
        self._limits = httpx.Limits(
            max_connections=max(int(max_connections), 1),
            max_keepalive_connections=max(int(max_keepalive_connections), 0),
            keepalive_expiry=float(keepalive_expiry),
        )
        self._lock = threading.Lock()
        self._clients: dict[str, httpx.Client] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _make_client(self, *, base_url: str, timeout: httpx.Timeout) -> httpx.Client:
        kwargs: dict = {
            "base_url": base_url,
            "timeout": timeout,
            "follow_redirects": False,
            "limits": self._limits,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _get_pooled(self, base_url: str, timeout: httpx.Timeout) -> httpx.Client:
        key = base_url.rstrip("/")
        with self._lock:
            client = self._clients.get(key)
            if client is not None and not client.is_closed:
                return client
            client = self._make_client(base_url=key, timeout=timeout)
            self._clients[key] = client
            logger.info(
                "http_pool.client_created",
                extra={"fields": {"base_url": key, "enabled": True}},
            )
            return client

    @contextmanager
    def acquire(self, *, base_url: str, timeout: httpx.Timeout) -> Iterator[httpx.Client]:
        # Tests inject a MockTransport — always use ephemeral clients so mocks stay isolated,
        # even when pooling is enabled.
        if self._transport is not None or not self._enabled:
            client = self._make_client(base_url=base_url.rstrip("/"), timeout=timeout)
            try:
                yield client
            finally:
                client.close()
            return

        client = self._get_pooled(base_url, timeout)
        yield client

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                logger.exception("http_pool.close_failed")
