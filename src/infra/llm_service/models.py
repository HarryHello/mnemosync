"""LLM 服务商数据模型.

与文档 modules/llm-service.md 对应.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal


class ModelType(StrEnum):
    """模型角色."""

    MAIN = "main"            # 主模型：主对话 Agent
    ASSIST = "assist"        # 辅助模型：记忆分析/关系分析/代理思考 Agent
    EMBEDDING = "embedding"  # 嵌入模型：向量化
    RERANK = "rerank"        # 重排序模型


# 上游 API 格式: openai (Chat Completions) | anthropic (Messages) | responses (Responses API)
ApiFormat = Literal["openai", "anthropic", "responses"]


@dataclass
class LLMServiceProvider:
    """模型服务商."""

    id: str
    base_url: str
    api_key: str  # 明文（读取时解密）
    api_format: ApiFormat = "openai"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def api_key_masked(self) -> str:
        if len(self.api_key) <= 10:
            return "********"
        return f"{self.api_key[:6]}****{self.api_key[-4:]}"

    @classmethod
    def create(
        cls,
        service_id: str,
        base_url: str,
        api_key: str,
        api_format: ApiFormat = "openai",
    ) -> LLMServiceProvider:
        now = datetime.now(UTC)
        return cls(id=service_id, base_url=base_url, api_key=api_key,
                   api_format=api_format, created_at=now, updated_at=now)


@dataclass
class ModelConfiguration:
    """模型配置（绑定到服务商的角色+模型名）."""

    id: str
    service_id: str
    model: str
    model_type: ModelType
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, service_id: str, model: str, model_type: ModelType) -> ModelConfiguration:
        now = datetime.now(UTC)
        return cls(id=secrets.token_hex(16), service_id=service_id, model=model,
                   model_type=model_type, created_at=now, updated_at=now)


@dataclass
class RoleBinding:
    """角色 → (服务商, 模型) 的优先级绑定.

    每个角色 (main/assist/embedding/rerank) 可以有多条候选, priority 越小优先级越高.
    Forwarder 按 priority 升序尝试, 遇上游错误 fallback 到下一条.
    嵌入角色特殊: 只允许一条绑定 (换模型会破坏已存向量的语义空间).
    """

    role: ModelType
    priority: int
    service_id: str
    model: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    context_length: int | None = None
    embedding_dim: int | None = None
    # v0.2.8: 是否把 embedding_dim 作为 `dimensions` 参数透传给上游.
    # 仅对可变维模型 (text-embedding-3-*, text-embedding-v3/v4, qwen3-embedding-*, ...)
    # 有意义; 固定维模型 (bge-*, bce-*, jina-*, mistral, gemini, ...) 上游会拒绝该参数.
    # 默认 False = 只作为向量库维度锁定的 metadata, 不透传上游.
    send_dimensions: bool = False
    # v0.4: 模态能力声明
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])


@dataclass
class ResolvedCandidate:
    """RoleResolver 返回的完整候选, 已解密 api_key."""

    role: ModelType
    priority: int
    service_id: str
    base_url: str
    api_key: str
    model: str
    api_format: ApiFormat = "openai"
    context_length: int | None = None
    embedding_dim: int | None = None
    send_dimensions: bool = False

    # 工具调用能力声明 (v0.3.1)
    supports_tools: bool = True          # 是否支持 tools 参数
    supports_stream_tools: bool = True   # 是否支持 stream=True + tools
    supports_parallel_tool_calls: bool = True
    supports_tool_choice_required: bool = True  # 是否支持 tool_choice="required"

    # v0.4: 模态能力声明
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
