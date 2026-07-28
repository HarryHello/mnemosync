"""管理 API 路由 - 记忆管理 + 关系 + 重索引 + 清理.

提供长期记忆的 CRUD、关系状态查询/更新/审计、批量删除、
向量库重建 (reindex)、衰减清理 (prune) 接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.deps import (
    get_identity_store,
    get_lorebook_store,
    get_memory_store,
    get_multi_forwarder,
    get_reindex_progress,
    get_resolver,
    get_vector_store,
)
from src.api.schemas.admin import (
    LorebookEntryCreateBody,
    LorebookEntryItem,
    LorebookEntryListResponse,
    LorebookEntryUpdateBody,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PruneBreakdown as PruneBreakdownSchema,
)
from src.api.schemas.admin import (
    PruneResponse,
    PruneStartBody,
    ReindexStartBody,
    ReindexStatusResponse,
)
from src.core.config import get_settings
from src.core.constants import DEFAULT_PERSONA_ID
from src.core.memory.models import Relationship
from src.core.memory.reindex import Pruner, Reindexer
from src.core.models.resolver import RoleResolver
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


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
    source_user: str
    created_at: str
    last_accessed_at: str | None


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int
    page: int = 1
    page_size: int = 50


class RelationshipIdentityAccount(BaseModel):
    """关系关联的一个平台账号."""

    actor_id: str
    frontend: str
    external_key: str
    display_name: str | None = None


class RelationshipIdentity(BaseModel):
    """effective_user_id 对应的可读身份信息."""

    kind: str  # actor | group
    name: str | None = None
    accounts: list[RelationshipIdentityAccount] = Field(default_factory=list)


class RelationshipResponse(BaseModel):
    persona_id: str
    user_id: str
    identity: RelationshipIdentity | None = None
    intimacy: float
    trust: float
    relationship_type: str | None
    notes: str | None
    updated_at: str
    # v0.2.10: 动态称呼演化. 序列化时保证非 None (若表中为 NULL 则填 TOML 基线值),
    # 前端拿到的永远是"当前有效值".
    persona_addressing: str
    user_addressing: str
    context: str


class RelationshipUpdateBody(BaseModel):
    """v0.2.10: 人工 override 关系称呼/背景 (source='manual').

    三字段都可选传, 但至少一个非 None. reason 必填 (至少 5 字), 用于审计.
    v0.3.0: user_id / actor_id 至少一个; 传 actor_id 时自动解析为
    effective_user_id (绑定 UserGroup 的 Actor 落到组关系上).
    """

    persona_addressing: str | None = None
    user_addressing: str | None = None
    context: str | None = None
    reason: str = Field(..., min_length=5, max_length=500)
    user_id: str | None = Field(None, min_length=1, description="用户标识 (effective_user_id)")
    actor_id: str | None = Field(None, min_length=1, description="Actor ID, 自动解析为 effective_user_id")


class RelationshipAuditItem(BaseModel):
    id: int
    changed_at: str
    source: str
    field_name: str
    old_value: str | None
    new_value: str | None
    reason: str


class RelationshipAuditResponse(BaseModel):
    items: list[RelationshipAuditItem]


class RelationshipListResponse(BaseModel):
    """v0.3.0: 多用户关系列表 (分页 + 排序)."""

    items: list[RelationshipResponse]
    total: int
    page: int = 1
    page_size: int = 20


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
    (绑定 UserGroup 的 Actor 落到组关系上 — 面板上点任一平台账号都能查到
    "这个人"的关系)。两者都缺 → 400。
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
    """将关系数据转为 Response 模型, 自动处理 NULL 与 TOML 基线回退.

    通用逻辑: 表中为 NULL 的称呼字段用 settings.persona.relation.* 填充;
    无关系行时返回默认 stranger/0/0, updated_at=""。
    """
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


# ============================================================================
# Memories
# ============================================================================


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    source_user: str = Query(..., min_length=1, description="用户标识 (必填)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    memory_type: str | None = Query(None, description="normal | permanent"),
    sort_by: str = Query("created_at", description="created_at | last_accessed | importance | decay_rate | access_count | memory_type | source_user"),
    sort_order: str = Query("desc", description="asc | desc"),
    before: str | None = Query(None, description="ISO 时间，仅返回此时间之前创建的记忆"),
    after: str | None = Query(None, description="ISO 时间，仅返回此时间之后创建的记忆"),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """查询记忆列表 (服务器端分页 + 排序).

    total 是符合 source_user + memory_type 过滤的**全量匹配数**, 不是本页返回条数;
    前端据此计算总页数。sort_by 走白名单, 非法值退回 created_at。
    before/after 支持 ISO 时间范围过滤。
    """
    before_dt = None
    after_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid before timestamp: {before}")
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid after timestamp: {after}")
    offset = (page - 1) * page_size
    items, total = await store.list_page_for_user(
        source_user,
        limit=page_size,
        offset=offset,
        memory_type=memory_type,
        sort_by=sort_by,
        sort_order=sort_order,
        before=before_dt,
        after=after_dt,
    )
    return MemoryListResponse(
        items=[_memory_to_response(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """获取单条记忆详情."""
    memory = await store.get_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(memory)


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """删除记忆."""
    success = await store.delete(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "message": "Memory deleted"}


class MemoryCorrectBody(BaseModel):
    """记忆纠正请求体."""

    content: str = Field(..., min_length=1, description="纠正后的记忆内容")
    reason: str = Field("", description="纠正原因 (审计用)")


@router.post("/memories/{memory_id}/correct", response_model=MemoryResponse)
async def correct_memory(
    memory_id: str,
    body: MemoryCorrectBody,
    store: SqliteMemoryStore = Depends(get_memory_store),
    forwarder=Depends(get_multi_forwarder),
    resolver=Depends(get_resolver),
):
    """纠正一条记忆: 创建新记忆替代旧记忆 (软替代).

    旧记忆不物理删除, 标记 superseded_by = 新记忆 ID, 从向量库移除。
    新记忆继承旧记忆的 source_user / memory_type / visibility / space_id。
    """
    old = await store.get_by_id(memory_id)
    if not old:
        raise HTTPException(status_code=404, detail="Memory not found")
    if old.superseded_by:
        raise HTTPException(status_code=409, detail="Memory already superseded")

    from src.core.memory.models import MemoryEntry

    new_entry = MemoryEntry.create(
        content=body.content,
        role=old.role,
        source_user=old.source_user,
        memory_type=old.memory_type,
        importance=old.importance,
        decay_rate=old.decay_rate,
    )
    new_entry.visibility = old.visibility
    new_entry.custom_policies = old.custom_policies
    new_entry.emotional_tags = old.emotional_tags
    new_entry.space_id = old.space_id
    new_entry.related_memories = old.related_memories + [old.id]

    # 生成 embedding
    try:
        from src.infra.debug_context import use_agent
        with use_agent("memory_correct"):
            vecs = await forwarder.embed(new_entry.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e

    # 校验向量库嵌入锁
    from src.infra.llm_service.models import ModelType
    from src.infra.vector_store import VectorStoreLockError
    try:
        from src.api.deps import get_vector_store
        vector_store = get_vector_store()
        cand = await resolver.first(ModelType.EMBEDDING)
        vector_store.assert_embedding_matches(cand.service_id, cand.model, len(vecs[0]))
    except VectorStoreLockError as e:
        raise HTTPException(status_code=409, detail=f"Vector store lock mismatch: {e}") from e
    except Exception:
        pass  # 向量库不可用时仍允许保存 (无语义检索)

    # 保存新记忆
    await store.save(new_entry)
    try:
        vector_store.add(new_entry, vecs[0])
    except Exception:
        pass

    # 标记旧记忆被替代
    await store.mark_superseded(memory_id, new_entry.id)
    # 从向量库移除旧记忆 (使其不被语义检索)
    try:
        vector_store.delete(memory_id)
    except Exception:
        pass

    logger.info(
        "记忆纠正: %s -> %s (reason=%s)", memory_id, new_entry.id, body.reason,
    )
    return _memory_to_response(new_entry)


@router.get("/memories/{memory_id}/supersede-chain")
async def get_supersede_chain(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """获取记忆的替代链 (原始 -> 替代版本 -> 更新的替代 -> ...)."""
    chain = await store.get_supersede_chain(memory_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "items": [
            {
                "id": e.id,
                "content": e.content,
                "superseded_by": e.superseded_by,
                "created_at": e.created_at.isoformat(),
            }
            for e in chain
        ],
        "total": len(chain),
    }


@router.delete("/memories")
async def delete_memories_batch(
    source_user: str = Query(..., min_length=1, description="用户标识 (effective_user_id, 必填)"),
    memory_type: str | None = Query(None, description="可选: 仅删除指定类型 (permanent/normal)"),
    before: str | None = Query(None, description="可选: 仅删除此 ISO 时间之前创建的记忆"),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """批量删除指定用户的记忆.

    用户记忆治理基础端点: 支持按用户、记忆类型、创建时间批量删除。
    这是隐私合规的最小可用集, 允许管理员或用户自己删除指定记忆。
    """
    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid before timestamp: {before}")
    deleted = await store.delete_by_user(
        source_user,
        memory_type=memory_type,
        before=before_dt,
    )
    return {"success": True, "deleted": deleted, "message": f"Deleted {deleted} memories"}


# ============================================================================
# Relationship
# ============================================================================


@router.get("/relationship", response_model=RelationshipResponse)
async def get_relationship(
    request: Request,
    user_id: str | None = Query(None, min_length=1, description="用户标识 (effective_user_id)"),
    actor_id: str | None = Query(None, min_length=1, description="Actor ID, 自动解析为 effective_user_id"),
    store: SqliteMemoryStore = Depends(get_memory_store),
    identity_store: SqliteIdentityStore = Depends(get_identity_store),
):
    """获取关系状态.

    关系尚未建立 (新装 / 人格重置后 / 与新 user_id 首次交互) 时返回默认 stranger/0/0,
    不落库 — 后续对话时 `lifecycle.update_relationship` 会自然创建真实行.

    v0.2.10: 响应含 persona_addressing / user_addressing / context. 表中为 NULL 时
    用 settings.persona.relation.* 基线填充, 前端拿到的永远是"当前有效值".

    v0.3.0: user_id / actor_id 至少一个. actor_id 经 identity_store 解析为
    effective_user_id (绑定 UserGroup 的 Actor 查到的是组关系).
    """
    target = await _resolve_relationship_target(request, user_id, actor_id)
    rel = await store.get_relationship(_persona_id(), target)
    identity = await _relationship_identity(identity_store, target)
    return _relationship_to_response(rel, target, identity=identity)


@router.get("/relationships", response_model=RelationshipListResponse)
async def list_relationships(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query(
        "intimacy_score",
        description="intimacy_score | trust_level | interaction_count | last_active | user_id | type",
    ),
    sort_order: str = Query("desc", description="asc | desc"),
    store: SqliteMemoryStore = Depends(get_memory_store),
    identity_store: SqliteIdentityStore = Depends(get_identity_store),
):
    """分页列出当前人格的所有关系 (v0.3.0 多用户).

    默认按亲密度降序排列, 适合仪表盘"关系较好的用户"展示.
    sort_by 走白名单, 非法值退回 intimacy_score.
    """
    offset = (page - 1) * page_size
    rows, total = await store.list_relationships(
        _persona_id(),
        limit=page_size,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    identities = await identity_store.resolve_user_identities([r.user_id for r in rows])
    return RelationshipListResponse(
        items=[
            _relationship_to_response(
                r,
                r.user_id,
                identity=_relationship_identity_response(identities.get(r.user_id)),
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.put("/relationship", response_model=RelationshipResponse)
async def update_relationship_addressing(
    body: RelationshipUpdateBody,
    request: Request,
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """人工 override 关系称呼/背景 (source='manual').

    v0.2.10: 允许通过面板/CLI 修改 persona_addressing / user_addressing / context.
    - 三字段可选, 至少传一个非 None
    - reason 必填 (min 5), 写入审计日志
    - 相同值会被跳过, 不写 audit
    - 相同响应 shape 与 GET 一致

    v0.3.0: user_id / actor_id 至少一个; actor_id 自动解析为 effective_user_id.
    """
    provided = {
        "persona_addressing": body.persona_addressing,
        "user_addressing": body.user_addressing,
        "context": body.context,
    }
    if all(v is None for v in provided.values()):
        raise HTTPException(400, detail="至少需要传入一个字段")
    target = await _resolve_relationship_target(request, body.user_id, body.actor_id)
    try:
        await store.update_relationship_addressing(
            _persona_id(), target,
            persona_addressing=body.persona_addressing,
            user_addressing=body.user_addressing,
            context=body.context,
            source="manual",
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return await get_relationship(
        request=request,
        user_id=target,
        store=store,
        identity_store=request.app.state.identity_store,
    )


@router.get("/relationship/audit", response_model=RelationshipAuditResponse)
async def list_relationship_audit(
    request: Request,
    user_id: str | None = Query(None, min_length=1, description="用户标识 (effective_user_id)"),
    actor_id: str | None = Query(None, min_length=1, description="Actor ID, 自动解析为 effective_user_id"),
    limit: int = Query(20, ge=1, le=200),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """按时间倒序返回关系称呼字段的审计条目 (v0.2.10)."""
    target = await _resolve_relationship_target(request, user_id, actor_id)
    entries = await store.list_relationship_audit(_persona_id(), target, limit=limit)
    return RelationshipAuditResponse(
        items=[
            RelationshipAuditItem(
                id=e.id,
                changed_at=e.changed_at.isoformat(),
                source=e.source,
                field_name=e.field_name,
                old_value=e.old_value,
                new_value=e.new_value,
                reason=e.reason,
            )
            for e in entries
        ]
    )


# ============================================================================
# Memory reindex + prune (v0.2.4)
# ============================================================================


@router.post("/memory/reindex", response_model=ReindexStatusResponse)
async def start_memory_reindex(
    body: ReindexStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    forwarder=Depends(get_multi_forwarder),
    resolver: RoleResolver = Depends(get_resolver),
    progress=Depends(get_reindex_progress),
):
    """启动向量库重建 (异步背景任务). 已在运行返回 409."""
    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 已在运行中")

    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver, progress)

    import asyncio as _asyncio

    async def _run():
        try:
            await reindexer.run(
                prune=body.prune,
                priority_threshold=body.priority_threshold,
            )
        except Exception as e:
            logger.error("reindex 背景任务失败: %s", e)

    _asyncio.create_task(_run())
    return ReindexStatusResponse(**progress.snapshot())


@router.get("/memory/reindex/status", response_model=ReindexStatusResponse)
async def get_memory_reindex_status(
    progress=Depends(get_reindex_progress),
):
    return ReindexStatusResponse(**progress.snapshot())


@router.post("/memory/prune", response_model=PruneResponse)
async def prune_memories(
    body: PruneStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    progress=Depends(get_reindex_progress),
):
    """按衰减规则清理记忆. 与 reindex 互斥 (running 时返 409).

    PERMANENT 永不删; is_forgotten / expired / priority<threshold 命中删除.
    dry_run=true 只返回统计。
    """
    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 运行中, prune 暂不可执行")

    pruner = Pruner(memory_store, vector_store)
    result = await pruner.run(
        priority_threshold=body.priority_threshold,
        dry_run=body.dry_run,
    )
    return PruneResponse(
        total_before=result.total_before,
        would_delete=result.would_delete,
        deleted=result.deleted,
        breakdown=PruneBreakdownSchema(**result.breakdown.as_dict()),
    )


# ============================================================================
# Lorebook
# ============================================================================


@router.get("/lorebook", response_model=LorebookEntryListResponse)
async def list_lorebook_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    space_id: str | None = Query(None),
    sort_by: str = Query("created_at", description="created_at | priority | content"),
    sort_order: str = Query("desc"),
    lorebook_store=Depends(get_lorebook_store),
):
    """分页列出 Lorebook 条目."""
    offset = (page - 1) * page_size
    items, total = await lorebook_store.list_page(
        limit=page_size, offset=offset, space_id=space_id,
        sort_by=sort_by, sort_order=sort_order,
    )
    return LorebookEntryListResponse(
        items=[
            LorebookEntryItem(
                id=e.id,
                content=e.content,
                keywords=list(e.keywords),
                priority=e.priority,
                space_id=e.space_id,
                created_at=e.created_at.isoformat(),
                updated_at=e.updated_at.isoformat(),
            )
            for e in items
        ],
        total=total,
    )


@router.post("/lorebook")
async def create_lorebook_entry(
    body: LorebookEntryCreateBody,
    lorebook_store=Depends(get_lorebook_store),
):
    """创建 Lorebook 条目."""
    from src.persistence.lorebook_store import LorebookEntry
    entry = LorebookEntry.create(
        content=body.content,
        keywords=body.keywords,
        priority=body.priority,
        space_id=body.space_id,
    )
    entry.persona_version_id = body.persona_version_id
    await lorebook_store.save(entry)
    return LorebookEntryItem(
        id=entry.id,
        content=entry.content,
        keywords=list(entry.keywords),
        priority=entry.priority,
        space_id=entry.space_id,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


@router.delete("/lorebook/{entry_id}")
async def delete_lorebook_entry(
    entry_id: str,
    lorebook_store=Depends(get_lorebook_store),
):
    """删除 Lorebook 条目."""
    ok = await lorebook_store.delete(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lorebook entry not found")
    return {"success": True}
