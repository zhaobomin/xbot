"""运行时模型管理器。

支持动态切换模型，一个 provider 一个 base_url，多个可用模型。

使用方式:
    manager = ModelManager(config)
    print(manager.current_model)  # 当前模型
    print(manager.available_models)  # 可用模型列表
    manager.switch_model("glm-4-flash")  # 切换模型
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from xbot.config.schema import Config


class ModelManager:
    """运行时模型管理器。

    管理当前使用的模型和可用模型列表。
    支持通过命令动态切换模型。

    Attributes:
        _config: 配置对象
        _current_model: 当前使用的模型
        _available_models: 可用模型列表
        _base_url: Provider 的 base URL
    """

    def __init__(self, config: Config):
        """初始化模型管理器。

        Args:
            config: xbot 配置对象
        """
        self._config = config
        self._available_models = self._get_available_models()
        self._current_model = self._get_default_model()
        self._base_url = self._get_base_url()

        logger.info(
            f"ModelManager initialized: current={self._current_model}, "
            f"available={self._available_models}, base_url={self._base_url}"
        )

    def _get_available_models(self) -> list[str]:
        """获取可用模型列表。

        优先使用 available_models 配置，为空时回退到 model 字段。

        Returns:
            可用模型列表
        """
        available = self._config.agents.defaults.available_models
        if available:
            return list(available)
        # 回退：只用配置的 model
        return [self._config.agents.defaults.model]

    def _get_default_model(self) -> str:
        """获取默认模型。

        如果 available_models 非空，返回第一个；
        否则返回配置的 model。

        Returns:
            默认模型名称
        """
        available = self._config.agents.defaults.available_models
        if available:
            return available[0]
        return self._config.agents.defaults.model

    def _get_base_url(self) -> str:
        """获取 Provider 的 base URL。

        Returns:
            Base URL 字符串
        """
        from xbot.platform.config.provider_registry import get_provider_spec

        # 获取 provider 名称
        provider_name = self._config.agents.defaults.provider
        if provider_name == "auto":
            # 自动检测 provider
            provider_name = self._config.get_provider_name() or "anthropic"

        # 获取 provider 配置
        provider_attr = provider_name.replace("-", "_")
        provider_config = getattr(self._config.providers, provider_attr, None)

        if provider_config and provider_config.api_base:
            return provider_config.api_base

        # 使用 provider spec 的默认 base URL
        spec = get_provider_spec(provider_name)
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
