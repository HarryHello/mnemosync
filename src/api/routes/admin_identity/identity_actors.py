"""参与者 (Actor) 管理与 Actor ↔ Group 绑定."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_identity_store, get_relationship_store
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    ActorListResponse,
    ActorResponse,
    UserGroupListResponse,
    UserGroupResponse,
)
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.relationship_store import SqliteRelationshipStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/identity/actors", response_model=ActorListResponse)
async def list_actors(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorListResponse:
    """列出所有 Actor."""
    items, total = await store.list_actors(limit=limit, offset=offset)
    return ActorListResponse(
        items=[
            ActorResponse(
                id=a.id, external_key=a.external_key, frontend=a.frontend,
                display_name=a.display_name, metadata=a.metadata,
                created_at=a.created_at.isoformat() if a.created_at else "",
                updated_at=a.updated_at.isoformat() if a.updated_at else "",
            )
            for a in items
        ],
        total=total,
    )


@router.get("/identity/actors/{actor_id}", response_model=ActorResponse)
async def get_actor(
    actor_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorResponse:
    """获取单个 Actor."""
    a = await store.get_actor(actor_id)
    if a is None:
        raise HTTPException(404, detail="Actor 不存在")
    return ActorResponse(
        id=a.id, external_key=a.external_key, frontend=a.frontend,
        display_name=a.display_name, metadata=a.metadata,
        created_at=a.created_at.isoformat() if a.created_at else "",
        updated_at=a.updated_at.isoformat() if a.updated_at else "",
    )


# ============================================================================
# Actor ↔ Group Bindings
# ============================================================================


@router.post("/identity/actors/{actor_id}/groups/{group_id}")
async def bind_actor_to_group(
    actor_id: str,
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
    relationship_store: SqliteRelationshipStore = Depends(get_relationship_store),
) -> dict[str, Any]:
    """绑定 Actor 到 UserGroup.

    绑定后自动迁移 Actor 的现有关系数据到 UserGroup, 防止同一人出现
    两条独立关系 (绑定前以 actor_id 为 user_id, 绑定后以 group_id 为 user_id).
    """
    ok = await store.bind_actor_to_group(actor_id, group_id)
    if not ok:
        raise HTTPException(409, detail="绑定已存在或 Actor/Group 不存在")
    from src.core.constants import DEFAULT_PERSONA_ID
    migrated = await relationship_store.migrate_relationships_to_group(
        DEFAULT_PERSONA_ID, actor_id, group_id,
    )
    if migrated:
        logger.info(
            "关系迁移: actor=%s → group=%s, 迁移 %d 条",
            actor_id, group_id, migrated,
        )
    return {"success": True, "actor_id": actor_id, "group_id": group_id}


@router.delete("/identity/actors/{actor_id}/groups/{group_id}")
async def unbind_actor_from_group(
    actor_id: str,
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> dict[str, Any]:
    """解绑 Actor 从 UserGroup."""
    ok = await store.unbind_actor_from_group(actor_id, group_id)
    if not ok:
        raise HTTPException(404, detail="绑定不存在")
    return {"success": True}


@router.get("/identity/actors/{actor_id}/groups", response_model=UserGroupListResponse)
async def list_actor_groups(
    actor_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupListResponse:
    """列出 Actor 所属的所有 UserGroup."""
    groups = await store.list_actor_groups(actor_id)
    return UserGroupListResponse(
        items=[
            UserGroupResponse(
                id=g.id, name=g.name,
                created_at=g.created_at.isoformat() if g.created_at else "",
                updated_at=g.updated_at.isoformat() if g.updated_at else "",
            )
            for g in groups
        ],
        total=len(groups),
    )
