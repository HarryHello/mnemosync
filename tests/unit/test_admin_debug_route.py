"""调试面板路由测试.

覆盖:
- 未登录 → 401
- session-key: 首次生成 + 复用同一 key
- events / status / clear 基本行为
- events/{id} 404 分支
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin_debug import router as admin_debug_router
from src.api.routes.auth import get_current_user
from src.infra.debug_bus import DebugEventBus
from src.persistence.api_key_store import (
    API_KEY_SOURCE_PANEL_DEBUG,
    SqliteApiKeyStore,
)
from src.persistence.auth_store import User


@pytest.fixture
def api_key_store(tmp_path: Path) -> Iterator[SqliteApiKeyStore]:
    s = SqliteApiKeyStore(str(tmp_path / "k.db"))
    import asyncio

    asyncio.get_event_loop().run_until_complete(s.connect())
    yield s
    asyncio.get_event_loop().run_until_complete(s.close())


@pytest.fixture
def app(api_key_store: SqliteApiKeyStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_debug_router)
    app.include_router(outer)

    bus = DebugEventBus(capacity=10, grace_seconds=999.0)  # grace 大, 不干扰测试
    app.state.api_key_store = api_key_store
    app.state.debug_bus = bus

    def _user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.dependency_overrides[get_current_user] = _user
    return app


@pytest.fixture
def app_unauth(api_key_store: SqliteApiKeyStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_debug_router)
    app.include_router(outer)
    app.state.api_key_store = api_key_store
    app.state.debug_bus = DebugEventBus(capacity=10)
    return app


def test_all_debug_endpoints_require_auth(app_unauth: FastAPI) -> None:
    client = TestClient(app_unauth)
    for method, path in [
        ("POST", "/panel/admin/debug/session-key"),
        ("GET", "/panel/admin/debug/status"),
        ("GET", "/panel/admin/debug/events"),
        ("GET", "/panel/admin/debug/events/nope"),
        ("DELETE", "/panel/admin/debug/events"),
    ]:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


def test_session_key_first_call_creates_key(app: FastAPI, api_key_store: SqliteApiKeyStore) -> None:
    client = TestClient(app)
    resp = client.post("/panel/admin/debug/session-key")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"].startswith("sk-")
    assert data["note"].startswith("panel-debug")

    import asyncio

    keys = asyncio.get_event_loop().run_until_complete(
        api_key_store.list_all(source=API_KEY_SOURCE_PANEL_DEBUG)
    )
    assert len(keys) == 1
    assert keys[0].source == API_KEY_SOURCE_PANEL_DEBUG


def test_session_key_reuses_existing(app: FastAPI, api_key_store: SqliteApiKeyStore) -> None:
    client = TestClient(app)
    r1 = client.post("/panel/admin/debug/session-key").json()
    r2 = client.post("/panel/admin/debug/session-key").json()
    assert r1["id"] == r2["id"]
    assert r1["key"] == r2["key"]

    import asyncio

    keys = asyncio.get_event_loop().run_until_complete(
        api_key_store.list_all(source=API_KEY_SOURCE_PANEL_DEBUG)
    )
    assert len(keys) == 1  # 未新建


def test_status_reports_zero_subscribers(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/panel/admin/debug/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subscriber_count"] == 0
    assert data["buffer_capacity"] == 10


def test_events_empty_and_after_emit(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/panel/admin/debug/events")
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    # 手动 emit (但因 subscriber=0 会被 should_emit gate 挡掉 —— 这本身是设计)
    bus: DebugEventBus = app.state.debug_bus
    # 加个假订阅者绕过 gate
    import asyncio

    async def _do():
        sub_id, _ = await bus.subscribe()
        eid = bus.emit(
            direction="inbound_request",
            correlation_id="c1",
            url="/v1/x",
            body={"a": 1},
        )
        return sub_id, eid

    sub_id, eid = asyncio.get_event_loop().run_until_complete(_do())
    try:
        resp = client.get("/panel/admin/debug/events")
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["correlation_id"] == "c1"

        # detail
        detail = client.get(f"/panel/admin/debug/events/{eid}").json()
        assert detail["summary"]["id"] == eid

        # 404
        assert client.get("/panel/admin/debug/events/does-not-exist").status_code == 404

        # clear
        assert client.delete("/panel/admin/debug/events").status_code == 204
        assert client.get("/panel/admin/debug/events").json()["items"] == []
    finally:
        asyncio.get_event_loop().run_until_complete(bus.unsubscribe(sub_id))


def test_events_stream_is_not_shadowed_by_event_detail(app: FastAPI) -> None:
    """回归测试: /events/stream 曾被声明在 /events/{event_id} 之后, FastAPI 按声明
    顺序匹配路由, 导致 "stream" 被当作 event_id 匹配到 detail 端点并返回 404。
    修复方式: 把 /events/stream 挪到 /events/{event_id} 之前。这里用 HEAD 探测
    Allow 头, 只要拿到 GET 就说明路由存在 (不真的开 SSE, 避免测试挂起)。"""
    client = TestClient(app)
    resp = client.head("/panel/admin/debug/events/stream")
    # FastAPI 未显式声明 HEAD 时会返回 405, allow 头里带 GET; 若被 detail 端点抢走
    # 则会走到 detail → 404 (因 event_id="stream" 不存在)
    assert resp.status_code == 405
    assert "GET" in resp.headers.get("allow", "")
