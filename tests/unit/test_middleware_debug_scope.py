"""HttpLogMiddleware 只对 /v1/* 入站请求 emit 调试事件, 面板自身 /panel/* 请求
   (调试面板的轮询、SSE、鉴权路由等) 不进调试事件流。

否则打开调试面板本身就会把 GET /panel/admin/debug/events 之类的自查请求当成
"客户端 → Mnemosync" 的入站请求写进事件流, 干扰真实链路观测。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.middleware import HttpLogMiddleware
from src.api.state import AppState
from src.infra.debug_bus import DebugEventBus


def _build_app() -> tuple[FastAPI, DebugEventBus]:
    app = FastAPI()
    bus = DebugEventBus(capacity=100)
    app.state = AppState(debug_bus=bus, api_key_store=None)
    app.add_middleware(HttpLogMiddleware, debug=False)

    @app.get("/v1/ping")
    def v1_ping():
        return {"ok": True}

    @app.get("/panel/anything")
    def panel_anything():
        return {"ok": True}

    return app, bus


@pytest.mark.asyncio
async def test_v1_request_emits_debug_events_when_subscribed() -> None:
    app, bus = _build_app()
    # 先加一个订阅者让 should_emit() 通过 gate
    sub_id, _ = await bus.subscribe()
    try:
        client = TestClient(app)
        resp = client.get("/v1/ping")
        assert resp.status_code == 200
        events = bus.list_recent(limit=100)
        directions = [e.direction for e in events]
        # 至少要有 inbound_request 与 inbound_response 各一
        assert "inbound_request" in directions
        assert "inbound_response" in directions
        # 且都指向 /v1/ping
        for e in events:
            assert "/v1/ping" in e.url
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_panel_request_does_not_emit_debug_events() -> None:
    app, bus = _build_app()
    sub_id, _ = await bus.subscribe()
    try:
        client = TestClient(app)
        resp = client.get("/panel/anything")
        assert resp.status_code == 200
        events = bus.list_recent(limit=100)
        # 面板自己的请求不该进 debug bus, 否则打开调试面板会自我污染事件流
        assert events == []
    finally:
        await bus.unsubscribe(sub_id)
