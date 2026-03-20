"""Agent backend implementations.

This package contains different Agent backend implementations:
- LiteLLMBackend: Uses the existing AgentLoop with LiteLLM
- ClaudeSDKBackend: Uses the Claude Agent SDK
"""

from xbot.agent.backends.litellm_backend import LiteLLMBackend

__all__ = ["LiteLLMBackend"]

# Claude SDK backend is optional
try:
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    __all__.append("ClaudeSDKBackend")
except ImportError:
    pass