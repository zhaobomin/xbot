"""Tests for multi-provider web search."""

from urllib.parse import urlparse

import httpx
import pytest

from xbot.platform.config.schema import WebSearchConfig
from xbot.tools.web import WebSearchTool


def _tool(provider: str = "brave", api_key: str = "", base_url: str = "") -> WebSearchTool:
    return WebSearchTool(config=WebSearchConfig(provider=provider, api_key=api_key, base_url=base_url))


def _response(status: int = 200, json: dict | None = None) -> httpx.Response:
    """Build a mock httpx.Response with a dummy request attached."""
    r = httpx.Response(status, json=json)
    r._request = httpx.Request("GET", "https://mock")
    return r


def _allow_pinned_search(monkeypatch):
    async def fake_pin(url):
        hostname = urlparse(url).hostname or "example.com"
        return True, "", {hostname: "203.0.113.10"}

    monkeypatch.setattr("xbot.tools.web._validate_and_pin_url", fake_pin)


async def _async_pin_brave(url):
    return True, "", {"api.search.brave.com": "203.0.113.10"}


@pytest.mark.asyncio
async def test_brave_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "brave" in url
        assert kw["headers"]["X-Subscription-Token"] == "brave-key"
        return _response(json={
            "web": {"results": [{"title": "NanoBot", "url": "https://example.com", "description": "AI assistant"}]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    _allow_pinned_search(monkeypatch)
    tool = _tool(provider="brave", api_key="brave-key")
    result = await tool.execute(query="xbot", count=1)
    assert "NanoBot" in result
    assert "https://example.com" in result


@pytest.mark.asyncio
async def test_brave_search_uses_pinned_transport(monkeypatch):
    captured = {}

    class FakeTransport:
        def __init__(self, pinned_hosts, proxy=None):
            self.pinned_hosts = pinned_hosts
            self.proxy = proxy

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kw):
            return _response(json={"web": {"results": []}})

    monkeypatch.setattr("xbot.tools.web._PinnedAsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(
        "xbot.tools.web._validate_and_pin_url",
        _async_pin_brave,
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    tool = _tool(provider="brave", api_key="brave-key")
    result = await tool.execute(query="xbot", count=1)

    assert "No results" in result
    assert isinstance(captured["transport"], FakeTransport)
    assert captured["transport"].pinned_hosts == {"api.search.brave.com": "203.0.113.10"}
    assert captured["proxy"] is None


@pytest.mark.asyncio
async def test_brave_search_uses_configured_proxy_without_claiming_dns_pinning(monkeypatch):
    captured = {}

    class ForbiddenPinnedTransport:
        def __init__(self, *args, **kwargs):
            raise AssertionError("proxy-backed search must not use the pinned transport")

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kw):
            return _response(json={"web": {"results": []}})

    monkeypatch.setattr("xbot.tools.web._PinnedAsyncHTTPTransport", ForbiddenPinnedTransport)
    monkeypatch.setattr(
        "xbot.tools.web._validate_and_pin_url",
        _async_pin_brave,
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    tool = WebSearchTool(
        config=WebSearchConfig(provider="brave", api_key="brave-key"),
        proxy="http://127.0.0.1:7890",
    )
    result = await tool.execute(query="xbot", count=1)

    assert "No results" in result
    assert captured["proxy"] == "http://127.0.0.1:7890"
    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_tavily_search(monkeypatch):
    async def mock_post(self, url, **kw):
        assert "tavily" in url
        assert kw["headers"]["Authorization"] == "Bearer tavily-key"
        return _response(json={
            "results": [{"title": "OpenClaw", "url": "https://openclaw.io", "content": "Framework"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    _allow_pinned_search(monkeypatch)
    tool = _tool(provider="tavily", api_key="tavily-key")
    result = await tool.execute(query="openclaw")
    assert "OpenClaw" in result
    assert "https://openclaw.io" in result


@pytest.mark.asyncio
async def test_searxng_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "searx.example" in url
        return _response(json={
            "results": [{"title": "Result", "url": "https://example.com", "content": "SearXNG result"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    _allow_pinned_search(monkeypatch)
    tool = _tool(provider="searxng", base_url="https://searx.example")
    result = await tool.execute(query="test")
    assert "Result" in result


@pytest.mark.asyncio
async def test_duckduckgo_search(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "DDG Result", "href": "https://ddg.example", "body": "From DuckDuckGo"}]

    monkeypatch.setattr("xbot.tools.web.DDGS", MockDDGS, raising=False)
    import xbot.tools.web as web_mod
    monkeypatch.setattr(web_mod, "DDGS", MockDDGS, raising=False)

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)

    tool = _tool(provider="duckduckgo")
    result = await tool.execute(query="hello")
    assert "DDG Result" in result


@pytest.mark.asyncio
async def test_brave_fallback_to_duckduckgo_when_no_key(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "DuckDuckGo fallback"}]

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    tool = _tool(provider="brave", api_key="")
    result = await tool.execute(query="test")
    assert "Fallback" in result


@pytest.mark.asyncio
async def test_jina_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "s.jina.ai" in str(url)
        assert kw["headers"]["Authorization"] == "Bearer jina-key"
        return _response(json={
            "data": [{"title": "Jina Result", "url": "https://jina.ai", "content": "AI search"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    _allow_pinned_search(monkeypatch)
    tool = _tool(provider="jina", api_key="jina-key")
    result = await tool.execute(query="test")
    assert "Jina Result" in result
    assert "https://jina.ai" in result


@pytest.mark.asyncio
async def test_unknown_provider():
    tool = _tool(provider="unknown")
    result = await tool.execute(query="test")
    assert "unknown" in result
    assert "Error" in result


@pytest.mark.asyncio
async def test_default_provider_is_brave(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "brave" in url
        return _response(json={"web": {"results": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    _allow_pinned_search(monkeypatch)
    tool = _tool(provider="", api_key="test-key")
    result = await tool.execute(query="test")
    assert "No results" in result


@pytest.mark.asyncio
async def test_searxng_no_base_url_falls_back(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "fallback"}]

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    tool = _tool(provider="searxng", base_url="")
    result = await tool.execute(query="test")
    assert "Fallback" in result


@pytest.mark.asyncio
async def test_searxng_invalid_url():
    tool = _tool(provider="searxng", base_url="not-a-url")
    result = await tool.execute(query="test")
    assert "Error" in result
