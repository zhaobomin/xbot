"""Compatibility layer for pinned HTTP transports used by web_fetch.

This module isolates any reliance on httpx/httpcore private APIs so the main
web tool logic does not import those internals directly.
"""

from __future__ import annotations

from typing import Any

import httpx

_PRIVATE_IMPORT_ERROR: Exception | None = None

try:
    from httpx._config import DEFAULT_LIMITS, Proxy, create_ssl_context
    from httpx._transports.base import AsyncBaseTransport
    from httpx._transports.default import AsyncResponseStream, map_httpcore_exceptions

    _PRIVATE_IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - import behavior depends on httpx version
    DEFAULT_LIMITS = None
    Proxy = None
    create_ssl_context = None
    AsyncBaseTransport = object  # type: ignore[assignment]
    AsyncResponseStream = None
    map_httpcore_exceptions = None
    _PRIVATE_IMPORTS_OK = False
    _PRIVATE_IMPORT_ERROR = exc


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


class PinnedAsyncHTTPTransport(AsyncBaseTransport):
    """HTTPX transport that pins selected hostnames to already-validated IPs."""

    def __init__(self, pinned_hosts: dict[str, str], proxy: str | None = None):
        if not _PRIVATE_IMPORTS_OK:
            raise RuntimeError(f"httpx private transport APIs unavailable: {_PRIVATE_IMPORT_ERROR}")

        import httpcore

        proxy_config = Proxy(url=proxy) if isinstance(proxy, str) else proxy
        ssl_context = create_ssl_context(verify=True, cert=None, trust_env=True)
        backend = PinnedAsyncNetworkBackend(pinned_hosts)

        if proxy_config is None:
            self._pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl_context,
                max_connections=DEFAULT_LIMITS.max_connections,
                max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
                keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
        elif proxy_config.url.scheme in ("http", "https"):
            self._pool = httpcore.AsyncHTTPProxy(
                proxy_url=httpcore.URL(
                    scheme=proxy_config.url.raw_scheme,
                    host=proxy_config.url.raw_host,
                    port=proxy_config.url.port,
                    target=proxy_config.url.raw_path,
                ),
                proxy_auth=proxy_config.raw_auth,
                proxy_headers=proxy_config.headers.raw,
                proxy_ssl_context=proxy_config.ssl_context,
                ssl_context=ssl_context,
                max_connections=DEFAULT_LIMITS.max_connections,
                max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
                keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
        elif proxy_config.url.scheme in ("socks5", "socks5h"):
            self._pool = httpcore.AsyncSOCKSProxy(
                proxy_url=httpcore.URL(
                    scheme=proxy_config.url.raw_scheme,
                    host=proxy_config.url.raw_host,
                    port=proxy_config.url.port,
                    target=proxy_config.url.raw_path,
                ),
                proxy_auth=proxy_config.raw_auth,
                ssl_context=ssl_context,
                max_connections=DEFAULT_LIMITS.max_connections,
                max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
                keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry,
                http1=True,
                http2=False,
                network_backend=backend,
            )
        else:  # pragma: no cover
            raise ValueError(f"Unsupported proxy scheme: {proxy_config.url.scheme!r}")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import httpcore

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
        with map_httpcore_exceptions():
            resp = await self._pool.handle_async_request(req)

        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=AsyncResponseStream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()

