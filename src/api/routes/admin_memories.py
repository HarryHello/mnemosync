"""管理 API 路由 - 记忆管理聚合器.

将子域路由组合到统一的路由下:
- admin_mem_shared.py           — 共享 Schema + Helper
- admin_memories_crud.py        — 记忆 CRUD
- admin_memories_relationships.py — 关系状态查询/更新/审计
- admin_memories_maintenance.py — 向量库重建 + 衰减清理
- 本文件                       — Lorebook 路由 + router 聚合

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_lorebook_store
from src.api.routes.auth import get_current_user
from src.persistence.lorebook_store import SqliteLorebookStore
from src.api.schemas.admin import (
    LorebookEntryCreateBody,
    LorebookEntryItem,
    LorebookEntryListResponse,
)

from .admin_memories_crud import router as crud_router
from .admin_memories_maintenance import router as maintenance_router
from .admin_memories_relationships import router as relationships_router

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)

# ── 子路由聚合 ──────────────────────────────────────────
router.include_router(crud_router)
router.include_router(relationships_router)
router.include_router(maintenance_router)


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
    lorebook_store: SqliteLorebookStore = Depends(get_lorebook_store),
) -> LorebookEntryListResponse:
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
    lorebook_store: SqliteLorebookStore = Depends(get_lorebook_store),
) -> LorebookEntryItem:
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
    lorebook_store: SqliteLorebookStore = Depends(get_lorebook_store),
) -> dict[str, Any]:
    """删除 Lorebook 条目."""
    ok = await lorebook_store.delete(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lorebook entry not found")
    return {"success": True}
