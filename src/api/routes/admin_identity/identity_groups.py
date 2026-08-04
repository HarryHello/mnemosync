"""用户组 (UserGroup) 管理."""

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_identity_store
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    ActorListResponse,
    ActorResponse,
    UserGroupCreateBody,
    UserGroupListResponse,
    UserGroupResponse,
)
from src.persistence.identity_store import SqliteIdentityStore

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/identity/groups", response_model=UserGroupListResponse)
async def list_user_groups(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupListResponse:
    """列出所有 UserGroup."""
    items, total = await store.list_groups(limit=limit, offset=offset)
    return UserGroupListResponse(
        items=[
            UserGroupResponse(
                id=g.id, name=g.name,
                created_at=g.created_at.isoformat() if g.created_at else "",
                updated_at=g.updated_at.isoformat() if g.updated_at else "",
            )
            for g in items
        ],
        total=total,
    )


@router.post("/identity/groups", response_model=UserGroupResponse, status_code=201)
async def create_user_group(
    body: UserGroupCreateBody,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupResponse:
    """创建 UserGroup."""
    g = await store.create_group(name=body.name)
    return UserGroupResponse(
        id=g.id, name=g.name,
        created_at=g.created_at.isoformat() if g.created_at else "",
        updated_at=g.updated_at.isoformat() if g.updated_at else "",
    )


@router.get("/identity/groups/{group_id}", response_model=UserGroupResponse)
async def get_user_group(
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupResponse:
    """获取单个 UserGroup."""
    g = await store.get_group(group_id)
    if g is None:
        raise HTTPException(404, detail="UserGroup 不存在")
    return UserGroupResponse(
        id=g.id, name=g.name,
        created_at=g.created_at.isoformat() if g.created_at else "",
        updated_at=g.updated_at.isoformat() if g.updated_at else "",
    )


@router.get("/identity/groups/{group_id}/members", response_model=ActorListResponse)
async def list_group_members(
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorListResponse:
    """列出 UserGroup 的所有成员 Actor."""
    members = await store.list_group_members(group_id)
    return ActorListResponse(
        items=[
            ActorResponse(
                id=a.id, external_key=a.external_key, frontend=a.frontend,
                display_name=a.display_name, metadata=a.metadata,
                created_at=a.created_at.isoformat() if a.created_at else "",
                updated_at=a.updated_at.isoformat() if a.updated_at else "",
            )
            for a in members
        ],
        total=len(members),
    )
