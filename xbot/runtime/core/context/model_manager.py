"""运行时模型管理器。

支持动态切换模型，一个 provider 一个 base_url，多个可用模型。
模型列表从 providers.{name}.models 读取。

使用方式:
    manager = ModelManager(config)
    print(manager.current_model)  # 当前模型
    print(manager.available_models)  # 可用模型列表
    manager.switch_model("glm-4-flash")  # 切换模型
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from xbot.platform.config.schema import Config


class ModelManager:
    """运行时模型管理器。

    管理当前使用的模型和可用模型列表。
    支持通过命令动态切换模型。

    模型列表来源优先级:
    1. providers.{current_provider}.models
    2. 硬编码默认值 (Anthropic: claude-sonnet-4-5 等)

    Attributes:
        _config: 配置对象
        _current_model: 当前使用的模型
        _available_models: 可用模型列表
        _base_url: Provider 的 base URL
    """

    # 硬编码默认模型列表（当供应商未配置 models 时使用）
    _DEFAULT_MODELS: dict[str, list[str]] = {
        "anthropic": ["claude-sonnet-4-5", "claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],
    }

    def __init__(self, config: Config):
        """初始化模型管理器。

        Args:
            config: xbot 配置对象
        """
        self._config = config
        self._provider_name = self._resolve_provider_name()
        self._available_models = self._get_available_models()
        self._current_model = self._get_default_model()
        self._base_url = self._get_base_url()

        logger.info(
            f"ModelManager initialized: provider={self._provider_name}, "
            f"current={self._current_model}, available={self._available_models}, "
            f"base_url={self._base_url}"
        )

    def _resolve_provider_name(self) -> str:
        """解析当前使用的供应商名称。

        Returns:
            供应商名称
        """
        provider_name = self._config.agents.defaults.provider
        if provider_name == "auto":
            provider_name = self._config.get_provider_name() or "anthropic"
        return provider_name

    def _get_provider_models(self) -> list[str]:
        """从当前供应商配置读取模型列表。

        Returns:
            模型列表，未配置时返回空列表
        """
        from xbot.platform.config.loader import _provider_name_to_snake
        provider_attr = _provider_name_to_snake(self._provider_name)

        # 先检查固定供应商
        provider_config = getattr(self._config.providers, provider_attr, None)

        # 再检查 custom_providers
        if not provider_config and hasattr(self._config.providers, 'custom_providers'):
            provider_config = self._config.providers.custom_providers.get(provider_attr)

        if provider_config and hasattr(provider_config, 'models'):
            return list(provider_config.models or [])

        return []

    def _get_available_models(self) -> list[str]:
        """获取可用模型列表。

        优先级:
        1. providers.{current_provider}.models
        2. 硬编码默认值

        Returns:
            可用模型列表
        """
        # 1. 从供应商配置读取
        models = self._get_provider_models()
        if models:
            return models

        # 2. 使用硬编码默认值
        if self._provider_name in self._DEFAULT_MODELS:
            return self._DEFAULT_MODELS[self._provider_name]

        # 3. 最后兜底
        return ["claude-sonnet-4-5"]

    def _get_default_model(self) -> str:
        """获取默认模型。

        优先级:
        1. agents.defaults.model (如果显式设置)
        2. providers.{current_provider}.models[0]
        3. 硬编码默认值

        Returns:
            默认模型名称
        """
        # 1. 如果用户显式设置了 model，使用它
        configured_model = self._config.agents.defaults.model
        if configured_model:
            return configured_model

        # 2. 使用供应商的第一个模型
        if self._available_models:
            return self._available_models[0]

        # 3. 硬编码兜底
        return "claude-sonnet-4-5"

    def _get_base_url(self) -> str:
        """获取 Provider 的 base URL。

        Returns:
            Base URL 字符串
        """
        from xbot.platform.config.provider_registry import get_provider_spec

        # 获取 provider 配置
        from xbot.platform.config.loader import _provider_name_to_snake
        provider_attr = _provider_name_to_snake(self._provider_name)
        provider_config = getattr(self._config.providers, provider_attr, None)

        if not provider_config and hasattr(self._config.providers, 'custom_providers'):
            provider_config = self._config.providers.custom_providers.get(provider_attr)

        if provider_config and provider_config.api_base:
            return provider_config.api_base

        # 使用 provider spec 的默认 base URL
        spec = get_provider_spec(self._provider_name)
        if spec and spec.default_base_url:
            return spec.default_base_url

        return "unknown"

    @property
    def current_model(self) -> str:
        """当前使用的模型。"""
        return self._current_model

    @property
    def base_url(self) -> str:
        """Provider 的 base URL。"""
        return self._base_url

    @property
    def available_models(self) -> list[str]:
        """可用模型列表。"""
        return list(self._available_models)

    def switch_model(self, model_id: str) -> tuple[bool, str]:
        """切换模型。

        Args:
            model_id: 目标模型 ID

        Returns:
            (success, message) 元组
        """
        if model_id not in self._available_models:
            available_str = ", ".join(self._available_models)
            return False, f"模型 '{model_id}' 不在可用列表中\n可用模型: {available_str}"

        old_model = self._current_model
        self._current_model = model_id

        logger.info(f"Model switched: {old_model} -> {model_id}")
        return True, f"✅ 已切换到模型 `{model_id}`"

    def get_status_text(self) -> str:
        """获取状态文本，用于 $model 命令显示。

        Returns:
            格式化的状态文本
        """
        lines = [
            f"**Provider**: `{self._provider_name}`",
            f"**Base URL**: `{self._base_url}`",
            "",
            "**可用模型**:",
        ]

        for model in self._available_models:
            if model == self._current_model:
                lines.append(f"  - `{model}` ✅")
            else:
                lines.append(f"  - `{model}`")

        return "\n".join(lines)
