"""admin_memories 共享的 Schema 与 Helper 函数.

被 memories_crud / relationships / maintenance 三个子模块共同依赖,
避免循环导入.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from src.core.config import get_settings
from src.core.constants import DEFAULT_PERSONA_ID
from src.core.memory.models import Relationship
from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)


# ============================================================================
# Schemas
# ============================================================================


class MemoryResponse(BaseModel):
    id: str
    content: str
    memory_type: str
    importance: float
    decay_rate: float
    access_count: int
    source_user: str = ""
    created_at: str
    last_accessed_at: str | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int
    page: int
    page_size: int


class RelationshipIdentityAccount(BaseModel):
    actor_id: str
    frontend: str | None = None
    external_key: str | None = None
    display_name: str | None = None


class RelationshipIdentity(BaseModel):
    kind: str  # "group" | "actor"
    name: str | None = None
    accounts: list[RelationshipIdentityAccount] = Field(default_factory=list)


class RelationshipResponse(BaseModel):
    persona_id: str
    user_id: str
    identity: RelationshipIdentity | None = None
    intimacy: float
    trust: float
    relationship_type: str
    notes: str | None = None
    updated_at: str
    persona_addressing: str = ""
    user_addressing: str = ""
    context: str = ""


class RelationshipUpdateBody(BaseModel):
    user_id: str | None = Field(None, min_length=1)
    actor_id: str | None = Field(None, min_length=1)
    persona_addressing: str | None = None
    user_addressing: str | None = None
    context: str | None = None
    reason: str = Field(..., min_length=5, description="修改原因 (审计用)")


class RelationshipAuditItem(BaseModel):
    id: int
    changed_at: str
    source: str
    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None


class RelationshipAuditResponse(BaseModel):
    items: list[RelationshipAuditItem]
    total: int


class RelationshipListResponse(BaseModel):
    items: list[RelationshipResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# Helpers
# ============================================================================


def _persona_id() -> str:
    """当前人格标识. v0.3.0 单人格阶段使用默认值, 未来从配置派生."""
    return DEFAULT_PERSONA_ID


async def _resolve_relationship_target(
    request: Request,
    user_id: str | None,
    actor_id: str | None,
) -> str:
    """解析关系端点的目标用户 (v0.3.0).

    user_id 优先直取; 否则 actor_id 经 identity_store 解析为 effective_user_id
    (绑定 UserGroup 的 Actor 落到组关系上). 两者都缺 → 400。
    """
    if user_id:
        return user_id
    if actor_id:
        identity_store: SqliteIdentityStore | None = getattr(
            request.app.state, "identity_store", None,
        )
        if identity_store is None:
            raise HTTPException(500, detail="identity store 未初始化")
        return await identity_store.get_effective_user_id(actor_id)
    raise HTTPException(400, detail="user_id 或 actor_id 至少提供一个")


def _memory_to_response(m) -> MemoryResponse:
    return MemoryResponse(
        id=m.id,
        content=m.content,
        memory_type=m.memory_type.value,
        importance=m.importance,
        decay_rate=m.decay_rate,
        access_count=m.access_count,
        source_user=m.source_user or "",
        created_at=m.created_at.isoformat() if m.created_at else "",
        last_accessed_at=m.last_accessed.isoformat() if m.last_accessed else None,
    )


def _relationship_identity_response(
    resolved: tuple | None,
) -> RelationshipIdentity | None:
    """将 IdentityStore 的批量解析结果转为 API 身份视图."""
    if resolved is None:
        return None
    group, actors = resolved
    return RelationshipIdentity(
        kind="group" if group is not None else "actor",
        name=group.name if group is not None else None,
        accounts=[
            RelationshipIdentityAccount(
                actor_id=actor.id,
                frontend=actor.frontend,
                external_key=actor.external_key,
                display_name=actor.display_name,
            )
            for actor in actors
        ],
    )


async def _relationship_identity(
    identity_store: SqliteIdentityStore,
    user_id: str,
) -> RelationshipIdentity | None:
    resolved = await identity_store.resolve_user_identities([user_id])
    return _relationship_identity_response(resolved.get(user_id))


def _relationship_to_response(
    rel: Relationship | None,
    target: str,
    *,
    settings_override=None,
    identity: RelationshipIdentity | None = None,
) -> RelationshipResponse:
    """将关系数据转为 Response 模型, 自动处理 NULL 与 TOML 基线回退."""
    s = settings_override or get_settings()
    base = s.persona.relation
    if not rel:
        return RelationshipResponse(
            persona_id=_persona_id(),
            user_id=target,
            identity=identity,
            intimacy=0.0,
            trust=0.0,
            relationship_type="stranger",
            notes=None,
            updated_at="",
            persona_addressing=base.persona_addressing,
            user_addressing=base.user_addressing,
            context=base.context,
        )
    return RelationshipResponse(
        persona_id=rel.persona_id,
        user_id=rel.user_id,
        identity=identity,
        intimacy=rel.intimacy_score,
        trust=rel.trust_level,
        relationship_type=rel.type,
        notes=rel.notes,
        updated_at=rel.last_active.isoformat() if rel.last_active else "",
        persona_addressing=rel.persona_addressing or base.persona_addressing,
        user_addressing=rel.user_addressing or base.user_addressing,
        context=rel.context or base.context,
    )
