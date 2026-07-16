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


class RoleBindingReorderBody(BaseModel):
    """按新优先级从高到低排序的 (service_id, model) 列表, 必须与现有绑定一一对应."""

    order: list[tuple[str, str]] = Field(..., description="[[service_id, model], ...]")


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
]
