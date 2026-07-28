"""管理 API 路由 - 跨前端对话流水管理.

提供对话轮次的分页查询、来源列表、单条/批量/时间范围删除接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import get_conversation_store
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    ConversationClearResponse,
    ConversationTurnItem,
    ConversationTurnListResponse,
)
from src.persistence.conversation_store import SqliteConversationStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Schemas
# ============================================================================


class ConversationDeleteBatchBody(BaseModel):
    ids: list[int]


# ============================================================================
# Conversation Turns
# ============================================================================


@router.get("/conversation-turns", response_model=ConversationTurnListResponse)
async def list_conversation_turns(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    role: str | None = Query(None, description="user | assistant, 省略=全部"),
    source_frontend: str | None = Query(
        None, description="精确匹配来源平台, 省略=全部"
    ),
    actor_id: str | None = Query(None),
    effective_user_id: str | None = Query(None),
    space_id: str | None = Query(None),
    origin: str | None = Query(None, description="current | history_snapshot | assistant | legacy"),
    sort_by: str = Query(
        "ts",
        description="ts | role | token_count | source_frontend | origin | display_name_snapshot | committed_sequence | id",
    ),
    sort_order: str = Query("desc", description="asc | desc"),
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """按 ts 降序分页列出跨前端对话流水.

    面板 "上下文流水" 视图用. 服务器把所有前端的对话汇聚到这里, 装填时
    按时间窗 + 模型窗双窗口从这里裁剪. sort_by 走白名单, 非法值退回 ts。
    """
    offset = (page - 1) * page_size
    turns, total = await store.list_page(
        limit=page_size,
        offset=offset,
        role=role,
        source_frontend=source_frontend,
        actor_id=actor_id,
        effective_user_id=effective_user_id,
        space_id=space_id,
        origin=origin,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = [
        ConversationTurnItem(
            id=t.id or 0,
            role=t.role,
            content=t.content,
            ts=t.ts.isoformat(),
            token_count=t.token_count,
            source_frontend=t.source_frontend,
            actor_id=t.actor_id,
            effective_user_id=t.effective_user_id,
            display_name=t.display_name_snapshot,
            external_key=t.external_key_snapshot,
            space_id=t.space_id,
            external_event_id=t.external_event_id,
            origin=t.origin,
            event_fingerprint=t.event_fingerprint,
            observed_at=(t.observed_at or t.ts).isoformat(),
            request_id=t.request_id,
            committed_sequence=t.committed_sequence,
            late_arrival=t.late_arrival,
        )
        for t in turns
    ]
    return ConversationTurnListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/conversation-turns/sources")
async def list_conversation_turn_sources(
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """列出流水里出现过的所有来源标签 (source_frontend distinct).

    面板 "来源" 列 header filter 用. NULL / 空串排除 (视为 "未标注")
    """
    return {"items": await store.list_source_frontends()}


@router.delete("/conversation-turns/{turn_id}")
async def delete_conversation_turn(
    turn_id: int,
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """删除单条对话轮次. 用于面板逐条清理.

    注意: 不动其它前端已经拿到的上下文; 但下一次装填时该条从服务器视角"从未存在"
    """
    ok = await store.delete_by_id(turn_id)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation turn not found")
    return {"success": True, "id": turn_id}


@router.post("/conversation-turns/batch-delete")
async def batch_delete_conversation_turns(
    body: ConversationDeleteBatchBody,
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """批量删除指定 id 的对话轮次."""
    deleted = await store.delete_by_ids(body.ids)
    return {"success": True, "deleted": deleted}


@router.delete("/conversation-turns", response_model=ConversationClearResponse)
async def clear_conversation_turns(
    since_iso: str | None = Query(None, alias="since", description="ISO 时间, 只清早于该时间的记录 (省略则全清)"),
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """清空跨前端对话流水.

    * 不带 since → 全清 ("重置连续记忆")
    * 带 since → 只清 ts < since (面板 "只留最近 3 天")

    注意: 这不是"客户端 UI 清空对话" — 那种只是前端自己的显示状态, 服务器
    仍保有连续记忆. 只有面板操作或本端点才能真正把服务器的流水抹掉。
    """
    if since_iso:
        try:
            cutoff = datetime.fromisoformat(since_iso)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid since: {since_iso!r}")
        deleted = await store.delete_before(cutoff)
    else:
        deleted = await store.delete_all()
    return ConversationClearResponse(deleted=deleted)
