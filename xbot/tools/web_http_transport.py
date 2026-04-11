"""Pinned HTTP transports used by web_fetch.

This module intentionally relies on public httpx/httpcore APIs only.
"""

from __future__ import annotations

import contextlib
import ssl
from typing import Any

import httpcore
import httpx


class PinnedAsyncNetworkBackend:
    """Resolve approved hostnames once and connect using the pinned IP."""

    def __init__(self, pinned_hosts: dict[str, str], backend: Any | None = None):
        from httpcore._backends.auto import AutoBackend

        self._pinned_hosts = pinned_hosts
        self._backend = backend or AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any | None = None,
    ) -> Any:
        target_host = self._pinned_hosts.get(host, host)
        return await self._backend.connect_tcp(
            target_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any | None = None,
    ) -> Any:
        return await self._backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """HTTPX transport that pins selected hostnames to already-validated IPs."""

    def __init__(
        self,
        pinned_hosts: dict[str, str],
        proxy: str | None = None,
        max_response_bytes: int = 10 * 1024 * 1024,
    ):
        limits = httpx.Limits()
        ssl_context = ssl.create_default_context()
        backend = PinnedAsyncNetworkBackend(pinned_hosts)
        self._max_response_bytes = max_response_bytes

        max_connections = limits.max_connections or 100
        max_keepalive_connections = limits.max_keepalive_connections or 20
        keepalive_expiry = limits.keepalive_expiry if limits.keepalive_expiry is not None else 5.0

        if proxy is None:
            self._pool: Any = httpcore.AsyncConnectionPool(
                ssl_context=ssl_context,
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                keepalive_expiry=keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
            return

        proxy_url = httpx.URL(proxy)
        auth: tuple[bytes, bytes] | None = None
        if proxy_url.username is not None:
            auth = (
                proxy_url.username.encode("utf-8"),
                (proxy_url.password or "").encode("utf-8"),
            )

        if proxy_url.scheme in ("http", "https"):
            self._pool = httpcore.AsyncHTTPProxy(
                proxy_url=httpcore.URL(
                    scheme=proxy_url.raw_scheme,
                    host=proxy_url.raw_host,
                    port=proxy_url.port,
                    target=proxy_url.raw_path,
                ),
                proxy_auth=auth,
                proxy_headers=None,
                proxy_ssl_context=ssl_context,
                ssl_context=ssl_context,
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                keepalive_expiry=keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
        elif proxy_url.scheme in ("socks5", "socks5h"):
            self._pool = httpcore.AsyncSOCKSProxy(
                proxy_url=httpcore.URL(
                    scheme=proxy_url.raw_scheme,
                    host=proxy_url.raw_host,
                    port=proxy_url.port,
                    target=proxy_url.raw_path,
                ),
                proxy_auth=auth,
                ssl_context=ssl_context,
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                keepalive_expiry=keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
        else:  # pragma: no cover
            raise ValueError(f"Unsupported proxy scheme: {proxy_url.scheme!r}")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            resp = await self._pool.handle_async_request(req)
        except Exception as exc:
            raise httpx.TransportError(f"Pinned transport request failed: {exc}") from exc

        try:
            content_buffer = bytearray()
            async for chunk in resp.stream:
                content_buffer.extend(chunk)
                if len(content_buffer) > self._max_response_bytes:
                    raise httpx.TransportError(
                        f"Pinned transport response exceeded size limit: "
                        f"{len(content_buffer)} > {self._max_response_bytes} bytes"
                    )
        finally:
            with contextlib.suppress(Exception):
                await resp.stream.aclose()

        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            content=bytes(content_buffer),
            extensions=resp.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
