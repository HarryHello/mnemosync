"""admin/memories relationship 子路由: 关系状态查询/更新/审计/列表."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.deps import get_identity_store, get_relationship_store
from src.api.routes.auth import get_current_user
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.memory_store import SqliteRelationshipStore

from .admin_mem_shared import (
    RelationshipAuditItem,
    RelationshipAuditResponse,
    RelationshipListResponse,
    RelationshipResponse,
    RelationshipUpdateBody,
    _persona_id,
    _relationship_identity,
    _relationship_identity_response,
    _relationship_to_response,
    _resolve_relationship_target,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/relationship", response_model=RelationshipResponse)
async def get_relationship(
    request: Request,
    user_id: str | None = Query(None, min_length=1, description="用户标识 (effective_user_id)"),
    actor_id: str | None = Query(None, min_length=1, description="Actor ID, 自动解析为 effective_user_id"),
    store: SqliteRelationshipStore = Depends(get_relationship_store),
    identity_store: SqliteIdentityStore = Depends(get_identity_store),
):
    """获取关系状态."""
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
    store: SqliteRelationshipStore = Depends(get_relationship_store),
    identity_store: SqliteIdentityStore = Depends(get_identity_store),
):
    """分页列出当前人格的所有关系."""
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
    store: SqliteRelationshipStore = Depends(get_relationship_store),
):
    """人工 override 关系称呼/背景 (source='manual')."""
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
        identity_store=get_identity_store(request),
    )


@router.get("/relationship/audit", response_model=RelationshipAuditResponse)
async def list_relationship_audit(
    request: Request,
    user_id: str | None = Query(None, min_length=1, description="用户标识 (effective_user_id)"),
    actor_id: str | None = Query(None, min_length=1, description="Actor ID, 自动解析为 effective_user_id"),
    limit: int = Query(20, ge=1, le=200),
    store: SqliteRelationshipStore = Depends(get_relationship_store),
):
    """按时间倒序返回关系称呼字段的审计条目."""
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
        ],
        total=len(entries),
    )
