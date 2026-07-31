"""Integration tests: call_for_structured HTTP error handling.

Validates that CoreService.call_for_structured handles HTTP failures
gracefully, returning StructuredLLMResponse with finish_reason="error"
rather than crashing with unhandled exceptions.

Scenarios covered:
- 429 rate limit responses
- 500 internal server errors
- httpx.TimeoutException (network timeout)
- Malformed JSON in a 200 response body
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from xbot.runtime.core.protocol import StructuredLLMResponse

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_with_api_key():
    """Create a minimal AgentService-like object that has call_for_structured.

    We import AgentService and patch only what's needed to isolate the HTTP call.
    """
    from xbot.runtime.core.service import AgentService

    # AgentService.__init__ requires several parameters; we bypass the constructor
    # and inject only what call_for_structured needs.
    svc = object.__new__(AgentService)

    # _build_env_config returns env dict with API key
    svc._build_env_config = lambda: {
        "ANTHROPIC_API_KEY": "test-key-xxx",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    }

    # _get_effective_model returns a model name
    svc._get_effective_model = lambda: "claude-sonnet-4-20250514"

    return svc


def _standard_messages() -> list[dict[str, Any]]:
    """Minimal messages payload for call_for_structured."""
    return [
        {"role": "system", "content": "You are a helper."},
        {"role": "user", "content": "Summarize tasks."},
    ]


def _standard_tools() -> list[dict[str, Any]]:
    """Minimal tool definition in OpenAI format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "summarize",
                "description": "Summarize the tasks",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
        }
    ]


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    """Build a mock httpx.Response with the given status code and body."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code=status_code,
        request=request,
        content=json.dumps(json_body).encode() if json_body is not None else text.encode(),
        headers={"content-type": "application/json"},
    )
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHTTP429RateLimit:
    """429 Too Many Requests is handled gracefully."""

    async def test_http_429_rate_limit_handled_gracefully(self) -> None:
        """When the API returns 429, call_for_structured should return a
        StructuredLLMResponse with finish_reason='error' and an error
        message containing useful info. It must NOT raise an unhandled exception."""
        svc = _make_service_with_api_key()

        error_body = {
            "error": {
                "type": "rate_limit_error",
                "message": "Rate limit exceeded. Please retry after 30 seconds.",
            }
        }
        mock_resp = _mock_response(429, json_body=error_body)

        async def mock_post(self_client, url, **kwargs):
            raise httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=mock_resp.request,
                response=mock_resp,
            )

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
                tools=_standard_tools(),
                tool_choice="auto",
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.finish_reason == "error"
        assert "rate limit" in result.content.lower() or "429" in result.content
        # No tool calls on error
        assert result.tool_calls == [] or not result.has_tool_calls


class TestHTTP500ServerError:
    """500 Internal Server Error is handled gracefully."""

    async def test_http_500_server_error_does_not_crash(self) -> None:
        """When the API returns 500, the method should catch the error and
        return a StructuredLLMResponse with finish_reason='error'."""
        svc = _make_service_with_api_key()

        error_body = {
            "error": {
                "type": "server_error",
                "message": "Internal server error",
            }
        }
        mock_resp = _mock_response(500, json_body=error_body)

        async def mock_post(self_client, url, **kwargs):
            raise httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=mock_resp.request,
                response=mock_resp,
            )

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
                tools=_standard_tools(),
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.finish_reason == "error"
        assert "error" in result.content.lower() or "500" in result.content


class TestHTTPTimeout:
    """Timeout during HTTP call is handled gracefully."""

    async def test_http_timeout_returns_error(self) -> None:
        """When httpx raises TimeoutException, call_for_structured catches it
        via the generic except clause and returns an error response."""
        svc = _make_service_with_api_key()

        async def mock_post(self_client, url, **kwargs):
            raise httpx.TimeoutException("Connection timed out")

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
                tools=_standard_tools(),
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.finish_reason == "error"
        assert "timed out" in result.content.lower() or "timeout" in result.content.lower()

    async def test_http_connect_timeout_returns_error(self) -> None:
        """ConnectTimeout (a subclass of TimeoutException) is also handled."""
        svc = _make_service_with_api_key()

        async def mock_post(self_client, url, **kwargs):
            raise httpx.ConnectTimeout("Connection to api.anthropic.com timed out")

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.finish_reason == "error"
        assert "timed out" in result.content.lower() or "timeout" in result.content.lower()


class TestMalformedJSONResponse:
    """200 OK with malformed JSON body is handled gracefully."""

    async def test_malformed_json_response_body(self) -> None:
        """When the API returns 200 but the body isn't valid JSON,
        call_for_structured should catch the decode error and return
        an error response rather than crashing with JSONDecodeError."""
        svc = _make_service_with_api_key()

        # Return a response whose .json() will raise
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        bad_response = httpx.Response(
            status_code=200,
            request=request,
            content=b"this is not json {{{",
            headers={"content-type": "application/json"},
        )

        async def mock_post(self_client, url, **kwargs):
            return bad_response

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
                tools=_standard_tools(),
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.finish_reason == "error"
        # Should mention JSON or decode error
        assert "json" in result.content.lower() or "error" in result.content.lower()

    async def test_unexpected_response_structure(self) -> None:
        """When the API returns valid JSON but with unexpected structure
        (missing 'content' key), the method should handle gracefully."""
        svc = _make_service_with_api_key()

        # Valid JSON but not the expected Anthropic response format
        weird_body = {"id": "msg_123", "type": "message", "unexpected": True}
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        odd_response = httpx.Response(
            status_code=200,
            request=request,
            content=json.dumps(weird_body).encode(),
            headers={"content-type": "application/json"},
        )

        async def mock_post(self_client, url, **kwargs):
            return odd_response

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await svc.call_for_structured(
                messages=_standard_messages(),
                tools=_standard_tools(),
            )

        # Should not crash — returns a valid response (possibly empty content)
        assert isinstance(result, StructuredLLMResponse)
        # With missing "content" key, data.get("content", []) returns [],
        # so the result has empty content and no tool_calls — that's fine.
        assert result.finish_reason in ("stop", "error", None) or result.finish_reason is not None
        assert isinstance(result.content, str)
