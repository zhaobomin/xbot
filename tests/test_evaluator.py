import pytest

from xbot.platform.providers.base import LLMResponse, ToolCallRequest
from xbot.platform.utils.evaluator import evaluate_response


class DummyCaller:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls = 0

    async def __call__(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", tool_calls=[])


def _eval_tool_call(should_notify: bool, reason: str = "") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(
                id="eval_1",
                name="evaluate_notification",
                arguments={"should_notify": should_notify, "reason": reason},
            )
        ],
    )


@pytest.mark.asyncio
async def test_should_notify_true() -> None:
    llm_call = DummyCaller([_eval_tool_call(True, "user asked to be reminded")])
    result = await evaluate_response("Task completed with results", "check emails", llm_call)
    assert result is True


@pytest.mark.asyncio
async def test_should_notify_false() -> None:
    llm_call = DummyCaller([_eval_tool_call(False, "routine check, nothing new")])
    result = await evaluate_response("All clear, no updates", "check status", llm_call)
    assert result is False


@pytest.mark.asyncio
async def test_fallback_on_error() -> None:
    class FailingCaller(DummyCaller):
        async def __call__(self, *args, **kwargs) -> LLMResponse:
            raise RuntimeError("provider down")

    llm_call = FailingCaller([])
    result = await evaluate_response("some response", "some task", llm_call)
    assert result is True


@pytest.mark.asyncio
async def test_no_tool_call_fallback() -> None:
    llm_call = DummyCaller([LLMResponse(content="I think you should notify", tool_calls=[])])
    result = await evaluate_response("some response", "some task", llm_call)
    assert result is True
