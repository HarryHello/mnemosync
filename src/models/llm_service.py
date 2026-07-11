"""LLM 服务提供商数据模型.

参考 cli.md 中的命令示例设计:
- ad-service: 添加提供商 (service_id, base_url, api_key)
- ls-service: 列出提供商 (service_id, base_url, api_key 脱敏)
- ls-models: 列出可用模型 (service_id)
- set-main-model: 设置主模型 (service_id, model)
- set-assist-model: 设置辅助模型 (service_id, model)
- test-model: 测试模型连接 (service_id, model)
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ModelType(str, Enum):
    """模型类型."""

    MAIN = "main"  # 主模型：生成回答
    ASSIST = "assist"  # 辅助模型：情绪分析、策略解析等


class LLMServiceError(Exception):
    """LLM 服务异常."""
    pass


class ServiceAlreadyExistsError(LLMServiceError):
    """服务已存在."""
    pass


class ServiceNotFoundError(LLMServiceError):
    """服务未找到."""
    pass


class ModelNotFoundError(LLMServiceError):
    """模型未找到."""
    pass


@dataclass
class LLMServiceProvider:
    """LLM 服务提供商.

    Attributes:
        id: 服务唯一标识 (如 "openai", "siliconflow")
        base_url: API 基础 URL (如 "https://api.openai.com/v1")
        api_key: API 密钥 (存储前需加密)
        created_at: 创建时间
        updated_at: 最后更新时间
    """

    id: str
    base_url: str
    api_key: str  # 加密后的字符串
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def api_key_masked(self) -> str:
        """脱敏显示 API Key (显示前 6 和后 4 字符)."""
        if len(self.api_key) <= 10:
            return "********"
        return f"{self.api_key[:6]}****{self.api_key[-4:]}"

    @classmethod
    def create(
        cls,
        service_id: str,
        base_url: str,
        api_key: str,
    ) -> "LLMServiceProvider":
        """创建新的服务提供商.

        Args:
            service_id: 服务唯一标识
            base_url: API 基础 URL
            api_key: API 密钥 (明文，存储前应加密)

        Returns:
            新创建的服务提供商实例
        """
        now = datetime.now(timezone.utc)
        return cls(
            id=service_id,
            base_url=base_url,
            api_key=api_key,
            created_at=now,
            updated_at=now,
        )

    def update_api_key(self, new_api_key: str) -> None:
        """更新 API 密钥.

        Args:
            new_api_key: 新的 API 密钥
        """
        self.api_key = new_api_key
        self.updated_at = datetime.now(timezone.utc)

    def update_base_url(self, new_base_url: str) -> None:
        """更新基础 URL.

        Args:
            new_base_url: 新的基础 URL
        """
        self.base_url = new_base_url
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class ModelConfiguration:
    """模型配置.

    管理每个服务提供商的主模型和辅助模型.

    Attributes:
        id: 唯一标识
        service_id: 关联的服务提供商 ID
        model: 模型名称 (如 "gpt-4", "Qwen/Qwen3.5-397B-A17B")
        model_type: 模型类型 (main/assist)
        created_at: 创建时间
        updated_at: 最后更新时间
    """

    id: str
    service_id: str
    model: str
    model_type: ModelType
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        service_id: str,
        model: str,
        model_type: ModelType,
    ) -> "ModelConfiguration":
        """创建新的模型配置.

        Args:
            service_id: 关联的服务提供商 ID
            model: 模型名称
            model_type: 模型类型

        Returns:
            新创建的模型配置实例
        """
        now = datetime.now(timezone.utc)
        return cls(
            id=secrets.token_hex(16),
            service_id=service_id,
            model=model,
            model_type=model_type,
            created_at=now,
            updated_at=now,
        )

    def update_model(self, new_model: str) -> None:
        """更新模型名称.

        Args:
            new_model: 新的模型名称
        """
        self.model = new_model
        self.updated_at = datetime.now(timezone.utc)
