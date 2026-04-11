"""Base LLM provider interface."""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from xbot.platform.logging.core import get_logger
from xbot.platform.utils.retry import RetryPolicy, run_with_retry

logger = get_logger(__name__)


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to an OpenAI-style tool_call payload."""
        tool_call = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }
        if self.provider_specific_fields:
            tool_call["provider_specific_fields"] = self.provider_specific_fields
        if self.function_provider_specific_fields:
            tool_call["function"]["provider_specific_fields"] = self.function_provider_specific_fields
        return tool_call


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1 etc.
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


@dataclass(frozen=True)
class GenerationSettings:
    """Default generation parameters for LLM calls.

    Stored on the provider so every call site inherits the same defaults
    without having to pass temperature / max_tokens / reasoning_effort
    through every layer.  Individual call sites can still override by
    passing explicit keyword arguments to chat() / chat_with_retry().
    """

    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """

    _CHAT_RETRY_DELAYS = (1, 2, 4)
    _TRANSIENT_ERROR_MARKERS = (
        "429",
        "rate limit",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "temporarily unavailable",
    )

    _SENTINEL = object()

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
        self.generation: GenerationSettings = GenerationSettings()

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            tool_choice: Tool selection strategy ("auto", "required", or specific tool dict).

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    @classmethod
    def _is_transient_error(cls, content: str | None) -> bool:
        err = (content or "").lower()
        return any(marker in err for marker in cls._TRANSIENT_ERROR_MARKERS)

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Replace image_url blocks with text placeholder. Returns None if no images found."""
        found = False
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_content = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        path = (b.get("_meta") or {}).get("path", "")
                        placeholder = f"[image: {path}]" if path else "[image omitted]"
                        new_content.append({"type": "text", "text": placeholder})
                        found = True
                    else:
                        new_content.append(b)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result if found else None

    async def _safe_chat(self, **kwargs: Any) -> LLMResponse:
        """Call chat() and preserve unexpected programming errors."""
        try:
            return await self.chat(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected exception from provider chat()")
            raise

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call chat() with retry on transient provider failures.

        Parameters default to ``self.generation`` when not explicitly passed,
        so callers no longer need to thread temperature / max_tokens /
        reasoning_effort through every layer.
        """
        if max_tokens is self._SENTINEL:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )

        class _RetryableLLMError(Exception):
            def __init__(self, response: LLMResponse):
                self.response = response
                super().__init__(response.content)

        async def _call() -> LLMResponse:
            response = await self._safe_chat(**kw)
            if response.finish_reason != "error":
                return response
            if not self._is_transient_error(response.content):
                stripped = self._strip_image_content(messages)
                if stripped is not None:
                    logger.warning("Non-transient LLM error with image content, retrying without images")
                    return await self._safe_chat(**{**kw, "messages": stripped})
                return response
            raise _RetryableLLMError(response)

        try:
            return await run_with_retry(
                RetryPolicy(
                    max_attempts=len(self._CHAT_RETRY_DELAYS) + 1,
                    base_delay=float(self._CHAT_RETRY_DELAYS[0]),
                    max_delay=float(self._CHAT_RETRY_DELAYS[-1]),
                    retryable_exceptions=(_RetryableLLMError,),
                    jitter=False,
                ),
                "provider-chat",
                _call,
                sleep_func=asyncio.sleep,
            )
        except _RetryableLLMError as exc:
            return exc.response

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
