"""管理 API 路由 - 通知中心.

提供通知的分页查询、未读计数、标记已读、删除接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_notification_store
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    MarkReadResponse,
    NotificationItem,
    NotificationListResponse,
    UnreadCountResponse,
)
from src.persistence.notification_store import Notification, NotificationStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Helpers
# ============================================================================


def _notification_to_item(n: Notification) -> NotificationItem:
    return NotificationItem(
        id=n.id or 0,
        created_at=n.created_at.isoformat(),
        level=n.level,
        category=n.category,
        title=n.title,
        message=n.message,
        meta=n.meta,
        read_at=n.read_at.isoformat() if n.read_at else None,
    )


# ============================================================================
# Notifications
# ============================================================================


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    store: NotificationStore = Depends(get_notification_store),
) -> NotificationListResponse:
    """按 created_at 降序分页列出通知. unread_only=True 只返回未读."""
    offset = (page - 1) * page_size
    rows, total = await store.list_page(
        limit=page_size, offset=offset, unread_only=unread_only,
    )
    unread = await store.count_unread()
    return NotificationListResponse(
        items=[_notification_to_item(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        unread_count=unread,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_notifications_unread_count(
    store: NotificationStore = Depends(get_notification_store),
) -> UnreadCountResponse:
    """轻量端点, 供前端 60s 轮询."""
    return UnreadCountResponse(unread_count=await store.count_unread())


@router.post("/notifications/{notification_id}/read", response_model=MarkReadResponse)
async def mark_notification_read(
    notification_id: int,
    store: NotificationStore = Depends(get_notification_store),
) -> MarkReadResponse:
    """标记单条已读. 已经是已读状态时返回 marked=0 (幂等)."""
    if await store.get(notification_id) is None:
        raise HTTPException(status_code=404, detail="notification not found")
    hit = await store.mark_read(notification_id)
    return MarkReadResponse(marked=1 if hit else 0)


@router.post("/notifications/mark-all-read", response_model=MarkReadResponse)
async def mark_all_notifications_read(
    store: NotificationStore = Depends(get_notification_store),
) -> MarkReadResponse:
    return MarkReadResponse(marked=await store.mark_all_read())


@router.delete("/notifications/read")
async def delete_read_notifications(
    store: NotificationStore = Depends(get_notification_store),
) -> dict[str, Any]:
    """删除全部已读通知. 未读不受影响."""
    return {"deleted": await store.delete_read()}


@router.delete("/notifications/{notification_id}")
async def delete_notification(
    notification_id: int,
    store: NotificationStore = Depends(get_notification_store),
) -> dict[str, Any]:
    ok = await store.delete_by_id(notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="notification not found")
    return {"success": True, "id": notification_id}
