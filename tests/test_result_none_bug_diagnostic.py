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


# ─── _dispatch fix verification ───


def _make_dispatch_svc():
    """Create a minimally-mocked AgentService for _dispatch testing."""
    from xbot.runtime.core.service import AgentService

    svc = AgentService.__new__(AgentService)
    svc._set_session_routing = MagicMock()
    svc._resolve_execution_cwd = MagicMock(return_value="/tmp")
    svc._publish_event = AsyncMock()
    svc._format_tool_hint = MagicMock(return_value="")
    svc._commands_loader = None
    svc._shared_resources = {}
    return svc


def _make_inbound_msg():
    return SimpleNamespace(
        session_key="test:chat",
        channel="feishu",
        chat_id="chat123",
        content="上海天气",
        metadata={},
        media=None,
    )


async def test_dispatch_fallback_publishes_content_when_result_empty():
    """FIX: _dispatch now publishes last_content_text as fallback
    when ResultMessage.result is empty but AssistantMessage has text."""
    from xbot.platform.bus.events import OutboundMessage
    from xbot.runtime.core.protocol import AgentResponse

    svc = _make_dispatch_svc()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    msg = _make_inbound_msg()

    # Simulate new CLI behavior:
    # 1. Thinking → progress
    # 2. AssistantMessage with text (event_type="content")
    # 3. ResultMessage with result=None (event_type="result", content="")
    thinking_response = AgentResponse(
        content="",
        progress_texts=["Thinking: 从天气网站获取到了上海的天气信息。"],
        event_type="thinking",
        finish_reason="stop",
    )
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
        yield thinking_response
        yield content_response
        yield result_response

    with patch.object(svc, "process", mock_process):
        await svc._dispatch(msg, bus)

    # The FIX: fallback publishes content_response text
    result_calls = [
        c for c in bus.publish_outbound.call_args_list
        if not c[0][0].content.startswith("❌")
    ]
    assert len(result_calls) == 1
    outbound = result_calls[0][0][0]
    assert isinstance(outbound, OutboundMessage)
    assert outbound.content == "上海今天天气晴朗，温度25°C"


async def test_dispatch_result_preferred_over_content_fallback():
    """When ResultMessage.result IS populated, it takes precedence over
    content fallback (backward compatibility with old CLI)."""
    from xbot.runtime.core.protocol import AgentResponse

    svc = _make_dispatch_svc()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    msg = _make_inbound_msg()

    # Simulate old CLI behavior: result IS populated
    content_response = AgentResponse(
        content="上海今天天气晴朗，温度25°C",
        event_type="content",
        finish_reason="stop",
    )
    result_response = AgentResponse(
        content="上海今天天气晴朗，温度25°C",  # populated from ResultMessage.result
        event_type="result",
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def mock_process(context):
        yield content_response
        yield result_response

    with patch.object(svc, "process", mock_process):
        await svc._dispatch(msg, bus)

    # Result message takes precedence — published during loop
    bus.publish_outbound.assert_called_once()
    outbound = bus.publish_outbound.call_args[0][0]
    assert outbound.content == "上海今天天气晴朗，温度25°C"


async def test_dispatch_no_publish_when_both_empty():
    """No fallback publish when neither result nor content has text."""
    from xbot.runtime.core.protocol import AgentResponse

    svc = _make_dispatch_svc()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    msg = _make_inbound_msg()

    # Only thinking, no actual content text
    thinking_response = AgentResponse(
        content="",
        progress_texts=["Thinking: ..."],
        event_type="thinking",
        finish_reason="stop",
    )
    result_response = AgentResponse(
        content="",
        event_type="result",
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def mock_process(context):
        yield thinking_response
        yield result_response

    with patch.object(svc, "process", mock_process):
        await svc._dispatch(msg, bus)

    result_calls = [
        c for c in bus.publish_outbound.call_args_list
        if not c[0][0].content.startswith("❌")
    ]
    assert len(result_calls) == 0  # no content to publish


# ─── _dispatch_direct fix verification ───


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
