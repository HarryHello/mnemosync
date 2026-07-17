"""Admin API 相关 Pydantic 模型.

Prompt 覆盖管理 / 备份 / 校验相关的 request & response schema.
其他 admin 模块 (logs / memories / relationship) 目前仍在 admin.py 内联,
待未来一并迁入.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromptSummary(BaseModel):
    """列表项."""

    name: str
    description: str
    placeholders: list[str]
    overridden: bool
    version: int = 0


class PromptDetail(BaseModel):
    """单项详情 (含 current/default 原文)."""

    name: str
    description: str
    placeholders: list[str]
    overridden: bool
    version: int = 0
    current: str = Field(..., description="当前生效原文 (含 frontmatter)")
    default: str = Field(..., description="默认版本原文 (含 frontmatter)")


class PromptWriteBody(BaseModel):
    """PUT body."""

    content: str = Field(..., description="完整 prompt 原文 (Markdown, 可含 YAML frontmatter)")


class PromptValidateResponse(BaseModel):
    ok: bool
    missing_placeholders: list[str] = Field(default_factory=list)
    error: str | None = None


class PromptHistoryItem(BaseModel):
    filename: str
    mtime: str
    size: int


class PromptHistoryResponse(BaseModel):
    items: list[PromptHistoryItem]


# ---- Role bindings (v0.2.3 起模型绑定单一真相源) ----------------------


class RoleBindingItem(BaseModel):
    """role_bindings 表的单条视图."""

    role: str = Field(..., description="main | assist | embedding | rerank")
    priority: int = Field(..., ge=0, description="0 为最高优先级")
    service_id: str
    model: str
    created_at: str
    context_length: int | None = Field(
        default=None, description="最大上下文 (token). 可选, 仅用于面板展示"
    )
    embedding_dim: int | None = Field(
        default=None, description="嵌入维度. 可选, 会传给上游 dimensions 参数"
    )


class RoleBindingListResponse(BaseModel):
    items: list[RoleBindingItem]


class RoleBindingAddBody(BaseModel):
    role: str
    service_id: str
    model: str
    priority: int | None = Field(
        default=None,
        ge=0,
        description="省略时排到末尾; 指定时后续条目自动让位",
    )
    context_length: int | None = Field(
        default=None,
        ge=1,
        description="可选; 最大上下文 (token), 仅面板展示",
    )
    embedding_dim: int | None = Field(
        default=None,
        ge=1,
        description="可选; 嵌入维度. embedding 角色会传给上游 dimensions 参数",
    )


class RoleBindingReorderBody(BaseModel):
    """按新优先级从高到低排序的 (service_id, model) 列表, 必须与现有绑定一一对应."""

    order: list[tuple[str, str]] = Field(..., description="[[service_id, model], ...]")


class ProbeDimensionBody(BaseModel):
    """探测嵌入模型的真实输出维度 (不落库)."""

    service_id: str
    model: str
    dimensions: int | None = Field(
        default=None, ge=1, description="可变维模型的期望维度 (可选)"
    )


class ProbeDimensionResponse(BaseModel):
    dimensions: int


class ReindexStartBody(BaseModel):
    """启动向量库重建."""

    prune: bool = Field(default=False, description="是否同步清理过时/衰减的记忆")
    priority_threshold: float = Field(
        default=0.05, ge=0.0, le=1.0, description="prune=True 时的最低优先级阈值"
    )


class ReindexStatusResponse(BaseModel):
    state: str = Field(..., description="idle | running | success | error")
    total: int = 0
    processed: int = 0
    pruned: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class PruneStartBody(BaseModel):
    priority_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    dry_run: bool = False


class PruneBreakdown(BaseModel):
    forgotten: int = 0
    expired: int = 0
    low_priority: int = 0


class PruneResponse(BaseModel):
    total_before: int
    would_delete: int
    deleted: int
    breakdown: PruneBreakdown


__all__ = [
    "PromptSummary",
    "PromptDetail",
    "PromptWriteBody",
    "PromptValidateResponse",
    "PromptHistoryItem",
    "PromptHistoryResponse",
    "RoleBindingItem",
    "RoleBindingListResponse",
    "RoleBindingAddBody",
    "RoleBindingReorderBody",
    "ProbeDimensionBody",
    "ProbeDimensionResponse",
    "ReindexStartBody",
    "ReindexStatusResponse",
    "PruneStartBody",
    "PruneBreakdown",
    "PruneResponse",
]
