"""Tests for Claude SDK provider/model resolver."""

from xbot.platform.config.schema import Config
from xbot.platform.config.sdk_resolver import (
    detect_provider_from_model,
    normalize_sdk_model_name,
    resolve_sdk_provider_and_model,
)


def test_detect_provider_from_model() -> None:
    assert detect_provider_from_model("claude-sonnet-4-5") == "anthropic"
    assert detect_provider_from_model("qwen-max") == "aliyun_coding_plan"
    assert detect_provider_from_model("glm-5") == "aliyun_coding_plan"
    assert detect_provider_from_model("alrun-qwen") == "alrun"
    assert detect_provider_from_model("unknown-model") == "anthropic"


def test_normalize_sdk_model_name_strips_legacy_prefix() -> None:
    assert normalize_sdk_model_name("anthropic/claude-sonnet-4-5", "anthropic") == "claude-sonnet-4-5"
    assert normalize_sdk_model_name("aliyun_coding_plan/glm-5", "aliyun_coding_plan") == "glm-5"
    assert normalize_sdk_model_name("alrun-qwen-max", "alrun") == "qwen-max"


def test_resolve_auto_prefers_model_detected_provider_when_key_exists() -> None:
    config = Config()
    config.agents.defaults.provider = "auto"
    config.agents.defaults.model = "qwen-max"
    config.providers.aliyun_coding_plan.api_key = "test-key"

    provider, model = resolve_sdk_provider_and_model(config, require_api_key=True)

    assert provider == "aliyun_coding_plan"
    assert model == "qwen-max"


def test_resolve_auto_falls_back_to_available_sdk_provider() -> None:
    config = Config()
    config.agents.defaults.provider = "auto"
    config.agents.defaults.model = "unknown-model"
    config.providers.anthropic.api_key = "anthropic-key"

    provider, model = resolve_sdk_provider_and_model(config, require_api_key=True)

    assert provider == "anthropic"
    assert model == "unknown-model"


def test_resolve_rejects_sdk_incompatible_provider() -> None:
    config = Config()
    config.agents.defaults.provider = "openrouter"
    config.agents.defaults.model = "claude-sonnet-4-5"
    config.providers.openrouter.api_key = "test-key"

    try:
        resolve_sdk_provider_and_model(config)
    except ValueError as exc:
        assert "not compatible" in str(exc)
    else:
        raise AssertionError("Expected ValueError for sdk-incompatible provider")
