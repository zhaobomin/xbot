"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import json
import socket
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest

from xbot.platform.config.schema import WebToolsConfig
from xbot.tools.web import WebFetchTool


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(url="http://169.254.169.254/computeMetadata/v1/")
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()
    def _resolve_localhost(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    with patch("xbot.platform.security.network.socket.getaddrinfo", _resolve_localhost):
        result = await tool.execute(url="http://localhost/admin")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_result_contains_untrusted_flag():
    """When fetch succeeds, result JSON must include untrusted=True and the banner."""
    tool = WebFetchTool()

    fake_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"


    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = fake_html
        headers = {"content-type": "text/html"}
        def raise_for_status(self): pass
        def json(self): return {}

    async def _fake_get(self, url, **kwargs):
        return FakeResponse()

    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_public), \
         patch("httpx.AsyncClient.get", _fake_get):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data.get("untrusted") is True
    assert "[External content" in data.get("text", "")


@pytest.mark.asyncio
async def test_web_fetch_can_disable_security_checks(tmp_path) -> None:
    tool = WebFetchTool(
        proxy=None,
        web_config=WebToolsConfig(disable_security_checks=True),
    )

    fake_html = "<html><head><title>Unsafe</title></head><body><p>Hello</p></body></html>"

    class FakeResponse:
        status_code = 200
        url = "https://github.com/trending"
        text = fake_html
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    async def _fake_get(self, url, **kwargs):
        return FakeResponse()

    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_private), \
         patch("httpx.AsyncClient.get", _fake_get):
        result = await tool.execute(url="https://github.com/trending")

    data = json.loads(result)
    assert data.get("error") is None
    assert data.get("status") == 200
    assert data.get("finalUrl") == "https://github.com/trending"


@pytest.mark.asyncio
async def test_web_fetch_blocks_redirect_before_following_private_target():
    tool = WebFetchTool()
    state: dict[str, object] = {"requested": [], "follow_redirects": None}

    class FakeResponse:
        def __init__(self, status_code: int, url: str, headers: dict[str, str] | None = None, text: str = ""):
            self.status_code = status_code
            self.url = url
            self.headers = headers or {}
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("bad status")

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            state["follow_redirects"] = kwargs.get("follow_redirects")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, **kwargs):
            state["requested"].append(url)
            if url == "http://safe.example/start":
                return FakeResponse(302, url, headers={"location": "http://internal.example/admin"})
            raise AssertionError(f"unexpected fetch of {url}")

    def _resolver(hostname, port, family=0, type_=0):
        if hostname == "safe.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        if hostname == "internal.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
        raise socket.gaierror(hostname)

    with patch.object(tool, "_fetch_jina", AsyncMock(return_value=None)), \
         patch("httpx.AsyncClient", FakeClient), \
         patch("xbot.platform.security.network.socket.getaddrinfo", _resolver):
        result = await tool.execute(url="http://safe.example/start")

    data = json.loads(result)
    assert "error" in data
    assert state["follow_redirects"] is False
    assert state["requested"] == ["http://safe.example/start"]


@pytest.mark.asyncio
async def test_pinned_network_backend_uses_resolved_ip_for_connection():
    from xbot.tools.web import _PinnedAsyncNetworkBackend

    calls: list[str] = []

    class FakeBackend:
        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            calls.append(host)
            return object()

        async def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
            raise AssertionError("unexpected unix socket")

        async def sleep(self, seconds: float) -> None:
            return None

    backend = _PinnedAsyncNetworkBackend({"example.com": "93.184.216.34"}, backend=FakeBackend())

    await backend.connect_tcp("example.com", 443)
    await backend.connect_tcp("other.example", 443)

    assert calls == ["93.184.216.34", "other.example"]


@pytest.mark.asyncio
async def test_web_fetch_use_jina_flag_disables_jina_path():
    tool = WebFetchTool(web_config=WebToolsConfig(disable_security_checks=True))
    tool.web_config.web_fetch_use_jina = False

    with patch.object(tool, "_fetch_jina", AsyncMock(return_value='{"unexpected": true}')) as m_jina, \
         patch.object(tool, "_fetch_readability", AsyncMock(return_value='{"extractor":"readability"}')):
        result = await tool.execute(url="https://example.com/search?q=1")

    assert m_jina.await_count == 0
    data = json.loads(result)
    assert data.get("extractor") == "readability"


@pytest.mark.asyncio
async def test_web_fetch_jina_request_encodes_full_target_url():
    tool = WebFetchTool(web_config=WebToolsConfig(disable_security_checks=True))

    target = "https://example.com/path/p%2Fq?foo=bar&lang=zh#frag"
    expected = f"https://r.jina.ai/{quote(target, safe='')}"
    state: dict[str, str] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"title": "ok", "content": "ok", "url": target}}

    async def _fake_get(self, url, **kwargs):
        state["requested_url"] = str(url)
        return FakeResponse()

    with patch("httpx.AsyncClient.get", _fake_get):
        result = await tool._fetch_jina(target, max_chars=200)

    data = json.loads(result)
    assert state["requested_url"] == expected
    assert data["finalUrl"] == target
