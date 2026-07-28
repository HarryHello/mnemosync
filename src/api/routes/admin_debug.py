"""调试面板专用路由.

  * POST   /panel/admin/debug/session-key  — 打开调试页时自动生成 (或复用)
                                              source='panel-debug' 的 API Key
  * GET    /panel/admin/debug/status       — 订阅数 / 缓冲区大小
  * GET    /panel/admin/debug/events       — 列表 (ring buffer 最近 N 条)
  * GET    /panel/admin/debug/events/stream — SSE, 实时推送新事件
  * GET    /panel/admin/debug/events/{id}  — 单条完整 body (含流式 assembled)
  * DELETE /panel/admin/debug/events       — 清空 buffer

所有端点通过父 admin router 的 `Depends(get_current_user)` 已获得鉴权。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.api.deps import get_api_key_store
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    DebugEventDetailResponse,
    DebugEventListResponse,
    DebugEventSummary,
    DebugSessionKeyResponse,
    DebugStatusResponse,
)
from src.infra.debug_bus import DebugEventBus
from src.persistence.api_key_store import (
    API_KEY_SOURCE_PANEL_DEBUG,
    ApiKey,
    SqliteApiKeyStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/debug",
    tags=["Admin", "Debug"],
    dependencies=[Depends(get_current_user)],
)


def _get_debug_bus(request: Request) -> DebugEventBus:
    bus = getattr(request.app.state, "debug_bus", None)
    if bus is None:
        raise HTTPException(status_code=500, detail="debug bus not initialized")
    return bus


@router.post(
    "/session-key",
    response_model=DebugSessionKeyResponse,
    summary="生成或复用调试面板 API Key",
)
async def get_session_key(
    store: SqliteApiKeyStore = Depends(get_api_key_store),
) -> DebugSessionKeyResponse:
    """打开调试面板时调用. 优先复用现有的 panel-debug key, 无则新建.

    Key 在关闭调试面板 (SSE 订阅数 grace 到期) 时统一被清理。
    """
    existing = await store.list_all(source=API_KEY_SOURCE_PANEL_DEBUG)
    # 找 key_full 非空 (即当前进程可解密) 且 active 的
    reusable = next(
        (k for k in existing if k.is_active and k.key_full),
        None,
    )
    if reusable is not None:
        return DebugSessionKeyResponse(
            id=reusable.id,
            key=reusable.key_full or "",
            note=reusable.note,
            created_at=reusable.created_at.isoformat(),
        )
    ts = datetime.now(UTC).strftime("%H%M%S")
    ak = ApiKey.generate(note=f"panel-debug ({ts})", source=API_KEY_SOURCE_PANEL_DEBUG)
    await store.save(ak)
    logger.info("生成 panel-debug key: %s (note=%s)", ak.id, ak.note)
    return DebugSessionKeyResponse(
        id=ak.id,
        key=ak.key_full or "",
        note=ak.note,
        created_at=ak.created_at.isoformat(),
    )


@router.get("/status", response_model=DebugStatusResponse)
async def debug_status(request: Request) -> DebugStatusResponse:
    bus = _get_debug_bus(request)
    return DebugStatusResponse(
        subscriber_count=bus.subscriber_count,
        buffer_size=len(bus.list_recent(limit=10_000)),
        buffer_capacity=bus._capacity,
    )


@router.get("/events", response_model=DebugEventListResponse)
async def list_events(request: Request, limit: int = 200) -> DebugEventListResponse:
    bus = _get_debug_bus(request)
    limit = max(1, min(limit, 1000))
    items = [DebugEventSummary(**vars(e)) for e in bus.list_recent(limit=limit)]
    return DebugEventListResponse(items=items)


@router.delete("/events", status_code=status.HTTP_204_NO_CONTENT)
async def clear_events(request: Request) -> None:
    bus = _get_debug_bus(request)
    bus.clear()


@router.get("/events/stream")
async def stream_events(request: Request):
    """SSE: 订阅新事件. 订阅数用于 panel-debug key 生命周期管理."""
    bus = _get_debug_bus(request)

    async def event_gen():
        sub_id, q = await bus.subscribe()
        try:
            # 首帧: 发一个 ready 事件, 让前端知道订阅成功
            yield f"event: ready\ndata: {json.dumps({'sub_id': sub_id})}\n\n".encode("utf-8")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    payload = json.dumps(vars(ev), ensure_ascii=False, default=str)
                    yield f"data: {payload}\n\n".encode("utf-8")
                except asyncio.TimeoutError:
                    # keep-alive comment (SSE 允许 : 开头的注释, 不会触发 onmessage)
                    yield b": keepalive\n\n"
        finally:
            await bus.unsubscribe(sub_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# 注意: /events/{event_id} 必须放在 /events/stream 之后, 否则 "stream" 会被
# 当作 event_id 匹配 (FastAPI 按声明顺序匹配路由), 导致 SSE 端点返回 404.
@router.get("/events/{event_id}", response_model=DebugEventDetailResponse)
async def get_event(request: Request, event_id: str) -> DebugEventDetailResponse:
    bus = _get_debug_bus(request)
    full = bus.get_full(event_id)
    if full is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return DebugEventDetailResponse(**full)
