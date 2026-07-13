"""Tests for pinned web HTTP transport."""

from __future__ import annotations

import httpx
import pytest

from xbot.tools.web_http_transport import PinnedAsyncHTTPTransport


class _FakeStream:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.closed = False

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk
        return _gen()

    async def aclose(self):
        self.closed = True


class _FakeResponse:
    def __init__(self, chunks: list[bytes]):
        self.status = 200
        self.headers = []
        self.extensions = {}
        self.stream = _FakeStream(chunks)


class _FakePool:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.last_response: _FakeResponse | None = None

    async def handle_async_request(self, _req):
        self.last_response = _FakeResponse(self._chunks)
        return self.last_response

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_pinned_transport_reads_small_response():
    transport = PinnedAsyncHTTPTransport({}, max_response_bytes=1024)
    pool = _FakePool([b"hello ", b"world"])
    transport._pool = pool

    req = httpx.Request("GET", "https://example.com")
    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert resp.content == b"hello world"
    assert pool.last_response is not None
    assert pool.last_response.stream.closed is True


@pytest.mark.asyncio
async def test_pinned_transport_rejects_oversized_response_and_closes_stream():
    transport = PinnedAsyncHTTPTransport({}, max_response_bytes=8)
    pool = _FakePool([b"12345", b"67890"])
    transport._pool = pool

    req = httpx.Request("GET", "https://example.com")
    with pytest.raises(httpx.TransportError, match="exceeded size limit"):
        await transport.handle_async_request(req)

    assert pool.last_response is not None
    assert pool.last_response.stream.closed is True


def test_pinned_transport_rejects_proxy_that_would_bypass_dns_pinning():
    with pytest.raises(ValueError, match="DNS pinning"):
        PinnedAsyncHTTPTransport(
            {"example.com": "93.184.216.34"},
            proxy="http://127.0.0.1:7890",
        )
