"""Diagnostic test to verify the ResultMessage.result=None bug and its fix.

Tests verify both the bug mechanism AND that the fix correctly publishes
content when ResultMessage.result is None but text content exists in
AssistantMessage (event_type="content").
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Bug mechanism verification ───


def test_convert_result_message_none_result_produces_empty_content():
    """When ResultMessage.result is None, _convert_result_message
    produces AgentResponse with content="" (empty string)."""
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)

    msg = SimpleNamespace(
        subtype="success",
        duration_ms=5000,
        duration_api_ms=3000,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        result=None,
        usage=None,
        stop_reason="end_turn",
        total_cost_usd=0.01,
    )

    response = svc._convert_result_message(msg)

    assert response.event_type == "result"
    assert response.content == ""
    assert not response.content  # falsy


def test_convert_result_message_with_string_result_produces_content():
    """When ResultMessage.result is a string, content is correctly populated."""
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)

    msg = SimpleNamespace(
        subtype="success",
        duration_ms=5000,
        duration_api_ms=3000,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        result="上海今天天气晴朗，温度25°C",
        usage=None,
        stop_reason="end_turn",
        total_cost_usd=0.01,
    )

    response = svc._convert_result_message(msg)

    assert response.event_type == "result"
    assert response.content == "上海今天天气晴朗，温度25°C"
    assert response.content  # truthy


def test_convert_result_message_preserves_api_error_status():
    """SDK 0.1.76 exposes API error HTTP status on ResultMessage."""
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)
    msg = SimpleNamespace(
        subtype="error",
        duration_ms=5000,
        duration_api_ms=3000,
        is_error=True,
        num_turns=1,
        session_id="test-session",
        result=None,
        usage=None,
        api_error_status=429,
    )

    response = svc._convert_result_message(msg)

    assert response.event_data["api_error_status"] == 429


# ─── process_direct fix verification ───


async def test_process_direct_fallback_returns_content_when_result_empty():
    """FIX: process_direct returns last_content_text as fallback
    when ResultMessage.result is empty."""
    from xbot.runtime.core.protocol import AgentResponse
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)
    svc._resolve_execution_cwd = MagicMock(return_value="/tmp")
    svc._register_direct_progress_callback = MagicMock()
    svc._unregister_direct_progress_callback = MagicMock()
    svc._should_release_ephemeral_client = MagicMock(return_value=False)
    svc._shared_resources = {}
    on_progress = AsyncMock()

    content_response = AgentResponse(
        content="上海今天天气晴朗，温度25°C",
        event_type="content",
        finish_reason="stop",
    )
    result_response = AgentResponse(
        content="",
        event_type="result",
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def mock_process(context):
        yield content_response
        yield result_response

    with patch.object(svc, "process", mock_process):
        result = await svc.process_direct(
            "上海天气",
            session_key="test:chat",
            channel="feishu",
            chat_id="chat123",
            on_progress=on_progress,
        )

    assert result == "上海今天天气晴朗，温度25°C"


async def test_process_direct_result_preferred_over_content():
    """When ResultMessage.result IS populated, process_direct
    returns it preferentially."""
    from xbot.runtime.core.protocol import AgentResponse
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)
    svc._resolve_execution_cwd = MagicMock(return_value="/tmp")
    svc._register_direct_progress_callback = MagicMock()
    svc._unregister_direct_progress_callback = MagicMock()
    svc._should_release_ephemeral_client = MagicMock(return_value=False)
    svc._shared_resources = {}
    on_progress = AsyncMock()

    content_response = AgentResponse(
        content="上海今天天气晴朗",
        event_type="content",
        finish_reason="stop",
    )
    result_response = AgentResponse(
        content="上海今天天气晴朗，温度25°C",  # populated
        event_type="result",
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def mock_process(context):
        yield content_response
        yield result_response

    with patch.object(svc, "process", mock_process):
        result = await svc.process_direct(
            "上海天气",
            session_key="test:chat",
            channel="feishu",
            chat_id="chat123",
            on_progress=on_progress,
        )

    assert result == "上海今天天气晴朗，温度25°C"  # result takes precedence


# ─── SDK verification ───


def test_sdk_result_message_result_default_is_none():
    """SDK's ResultMessage dataclass has result=None default."""
    from claude_agent_sdk.types import ResultMessage

    msg = ResultMessage(
        subtype="success",
        duration_ms=5000,
        duration_api_ms=3000,
        is_error=False,
        num_turns=1,
        session_id="test",
    )
    assert msg.result is None


def test_sdk_message_parser_result_none_when_absent():
    """SDK message_parser produces result=None when 'result' key is absent."""
    from claude_agent_sdk._internal.message_parser import parse_message

    data = {
        "type": "result",
        "subtype": "success",
        "duration_ms": 5000,
        "duration_api_ms": 3000,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test",
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }
    msg = parse_message(data)
    assert msg.result is None


def test_sdk_message_parser_result_populated_when_present():
    """SDK message_parser produces populated result when present."""
    from claude_agent_sdk._internal.message_parser import parse_message

    data = {
        "type": "result",
        "subtype": "success",
        "duration_ms": 5000,
        "duration_api_ms": 3000,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test",
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "result": "上海天气晴朗",
    }
    msg = parse_message(data)
    assert msg.result == "上海天气晴朗"
