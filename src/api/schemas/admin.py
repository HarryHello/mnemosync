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
        default=None,
        description="嵌入维度. 用作向量库维度锁; 是否透传上游由 send_dimensions 决定",
    )
    send_dimensions: bool = Field(
        default=False,
        description=(
            "v0.2.8: 是否把 embedding_dim 作为 `dimensions` 参数透传给上游. "
            "仅可变维模型 (text-embedding-3-*, text-embedding-v3/v4, qwen3-embedding-*) "
            "需要开启; 固定维模型 (bge-*, bce-*, jina-*, mistral, gemini) 开启会被上游拒绝"
        ),
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
        description="可选; 嵌入维度. 用作向量库维度锁, 独立于是否透传上游",
    )
    send_dimensions: bool = Field(
        default=False,
        description=(
            "是否透传 dimensions 给上游. 默认 False (兼容 bge/bce/jina/mistral/gemini); "
            "仅在可变维模型且希望指定输出维度时置 True"
        ),
    )


class RoleBindingReorderBody(BaseModel):
    """按新优先级从高到低排序的 (service_id, model) 列表, 必须与现有绑定一一对应."""

    order: list[tuple[str, str]] = Field(..., description="[[service_id, model], ...]")


class RoleBindingUpdateBody(BaseModel):
    """就地更新一条绑定的可编辑字段. role / priority 由 URL 定位, 不在此改.

    整型字段的三态语义:
    - 键缺失 (不下发): 保持原值
    - 键为 null: 清空为 NULL
    - 键为整数: 覆盖
    """

    service_id: str | None = None
    model: str | None = None
    context_length: int | None = Field(default=None, ge=1)
    embedding_dim: int | None = Field(default=None, ge=1)
    send_dimensions: bool | None = None

    model_config = {"protected_namespaces": ()}


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


class DebugSessionKeyResponse(BaseModel):
    """调试面板自动生成/复用的 API Key."""

    id: str
    key: str = Field(..., description="完整 API Key, 前端保存后调用 /v1 时携带")
    note: str
    created_at: str


class DebugEventSummary(BaseModel):
    """调试事件摘要 (面板列表用)."""

    id: str
    correlation_id: str
    ts: float
    direction: str
    method: str | None = None
    url: str
    port: int | None = None
    agent: str | None = None
    status: int | None = None
    duration_ms: float | None = None
    key_note: str | None = None
    headers: dict | None = None
    body_preview: dict | list | str | None = None
    body_full_size: int = 0
    is_truncated: bool = False


class DebugEventListResponse(BaseModel):
    items: list[DebugEventSummary]


class DebugEventDetailResponse(BaseModel):
    summary: DebugEventSummary
    body_full: dict | list | str | None = None
    stream_assembled: str | None = None
    stream_chunks_count: int = 0


class DebugStatusResponse(BaseModel):
    subscriber_count: int
    buffer_size: int
    buffer_capacity: int


class ConversationTurnItem(BaseModel):
    """跨前端对话流水的一条记录."""

    id: int
    role: str  # user | assistant
    content: str
    ts: str  # ISO datetime
    token_count: int
    source_frontend: str | None = None


class ConversationTurnListResponse(BaseModel):
    total: int
    items: list[ConversationTurnItem]
    page: int = 1
    page_size: int = 50


class ConversationClearResponse(BaseModel):
    deleted: int


class PersonaResetBody(BaseModel):
    """人格状态重置请求. dry_run=True 只统计不执行."""

    dry_run: bool = False


class PersonaResetResponse(BaseModel):
    """一次 persona/reset 的执行结果.

    非 dry_run 时任一步失败, 其他步骤已完成的 delete 不回滚, 错误累计到 errors.
    """

    dry_run: bool
    deleted_memories: int
    deleted_relationships: int
    deleted_conversation_turns: int
    vector_reset: bool
    errors: list[str] = Field(default_factory=list)


class PersonaConfigRelation(BaseModel):
    """人格关系框架 (v0.2.11). 与 PersonaConfigRead 嵌套."""
    persona_addressing: str = "人格"
    user_addressing: str = "用户"
    context: str = "AI 助手与用户"


class PersonaConfigRead(BaseModel):
    """面板人格编辑视图 (v0.2.11). 反映多层合并后的当前有效值."""
    name: str = "助手"
    prompt: str = "你是一个有记忆能力的 AI 助手。"
    relation: PersonaConfigRelation = Field(default_factory=PersonaConfigRelation)
    overridden: bool = False  # data/persona_override.toml 是否存在


class PersonaConfigUpdateBody(BaseModel):
    """PUT /admin/persona body. 三字段都可选, 但至少传一个."""
    name: str | None = None
    prompt: str | None = None
    relation: PersonaConfigRelation | None = None


# ---- 通知中心 (v0.2.13) ---------------------------------------------------


class NotificationItem(BaseModel):
    """一条通知. level / category 都是自由字符串, UI 按 category 展开细节."""

    id: int
    created_at: str
    level: str = Field(..., description="info | warning | error")
    category: str
    title: str
    message: str
    meta: dict | None = None
    read_at: str | None = None


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    total: int
    page: int
    page_size: int
    unread_count: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadResponse(BaseModel):
    marked: int


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
    "RoleBindingUpdateBody",
    "ProbeDimensionBody",
    "ProbeDimensionResponse",
    "ReindexStartBody",
    "ReindexStatusResponse",
    "PruneStartBody",
    "PruneBreakdown",
    "PruneResponse",
    "DebugSessionKeyResponse",
    "DebugEventSummary",
    "DebugEventListResponse",
    "DebugEventDetailResponse",
    "DebugStatusResponse",
    "ConversationTurnItem",
    "ConversationTurnListResponse",
    "ConversationClearResponse",
    "PersonaResetBody",
    "PersonaResetResponse",
    "PersonaConfigRelation",
    "PersonaConfigRead",
    "PersonaConfigUpdateBody",
    "NotificationItem",
    "NotificationListResponse",
    "UnreadCountResponse",
    "MarkReadResponse",
]
