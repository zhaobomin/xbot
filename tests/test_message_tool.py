import pytest

from xbot.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_tool_adapter_message_tool_uses_injected_context(tmp_path) -> None:
    from xbot.capabilities.tool_adapter import ToolAdapter

    sent = []

    class _Bus:
        async def publish_outbound(self, msg) -> None:
            sent.append(msg)

    tool_adapter = ToolAdapter(
        workspace=str(tmp_path),
        tools_config=None,
        shared_resources={"bus": _Bus()},
    )
    tool_adapter._register_xbot_tools()
    tool_adapter.set_tool_context(channel="telegram", chat_id="chat-1", message_id="msg-1")

    message_tool = tool_adapter.get_tool("message")
    assert message_tool is not None

    result = await message_tool.execute(content="hello")

    assert result == "Message sent to telegram:chat-1"
    assert len(sent) == 1
    assert sent[0].channel == "telegram"
    assert sent[0].chat_id == "chat-1"
    assert sent[0].reply_to == "msg-1"
    assert sent[0].metadata["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_message_tool_sets_reply_to_from_explicit_message_id() -> None:
    sent = []

    async def _send(msg) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send,
        default_channel="discord",
        default_chat_id="chat-1",
    )

    result = await tool.execute(content="hello", message_id="reply-123")

    assert result == "Message sent to discord:chat-1"
    assert sent[0].reply_to == "reply-123"
    assert sent[0].metadata["message_id"] == "reply-123"


@pytest.mark.asyncio
async def test_message_tool_start_turn_resets_sent_flag() -> None:
    sent = []

    async def _send(msg) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send,
        default_channel="telegram",
        default_chat_id="chat-1",
    )

    assert tool._sent_in_turn is False
    result = await tool.execute(content="hello")
    assert "Message sent to telegram:chat-1" in result
    assert tool._sent_in_turn is True

    tool.start_turn()
    assert tool._sent_in_turn is False
