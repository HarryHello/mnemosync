"""admin /conversation-turns 路由测试 (v0.2.6).

覆盖:
  * 未登录 → 401 (GET / DELETE)
  * GET 返回按 ts 降序的列表 + total 计数
  * DELETE 无 since → 全清
  * DELETE 带 since → 只清早于 cutoff
  * DELETE since 非法格式 → 400
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.persistence.auth_store import User
from src.persistence.conversation_store import SqliteConversationStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteConversationStore]:
    s = SqliteConversationStore(str(tmp_path / "conv.db"))
    asyncio.get_event_loop().run_until_complete(s.connect())
    yield s
    asyncio.get_event_loop().run_until_complete(s.close())


def _seed(store: SqliteConversationStore, entries: list[tuple[str, str, datetime]]) -> None:
    async def _run() -> None:
        for role, content, ts in entries:
            await store.append(role, content, token_count=len(content), ts=ts)
    asyncio.get_event_loop().run_until_complete(_run())


@pytest.fixture
def app(store: SqliteConversationStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    app.state = AppState(conversation_store=store)

    def _user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.dependency_overrides[get_current_user] = _user
    return app


@pytest.fixture
def app_unauth(store: SqliteConversationStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    app.state = AppState(conversation_store=store)
    return app


def test_conversation_routes_require_auth(app_unauth: FastAPI) -> None:
    client = TestClient(app_unauth)
    for method, path in [
        ("GET", "/panel/admin/conversation-turns"),
        ("DELETE", "/panel/admin/conversation-turns"),
    ]:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


def test_list_returns_descending_with_total(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", "第一句", now - timedelta(minutes=3)),
        ("assistant", "回复 1", now - timedelta(minutes=2)),
        ("user", "第二句", now - timedelta(minutes=1)),
    ])
    resp = TestClient(app).get("/panel/admin/conversation-turns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    contents = [it["content"] for it in body["items"]]
    assert contents == ["第二句", "回复 1", "第一句"]
    # 结构完整
    assert body["items"][0]["role"] == "user"
    assert "ts" in body["items"][0]
    assert "token_count" in body["items"][0]
    assert body["items"][0]["origin"] == "current"
    assert "actor_id" in body["items"][0]
    assert "space_id" in body["items"][0]
    assert "observed_at" in body["items"][0]


def test_list_respects_limit(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", str(i), now - timedelta(seconds=10 - i))
        for i in range(5)
    ])
    resp = TestClient(app).get("/panel/admin/conversation-turns?page_size=2")
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5  # total 反映全部匹配数, 不受 page_size 影响
    assert body["page"] == 1
    assert body["page_size"] == 2


def test_list_page_two_offsets_correctly(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", str(i), now - timedelta(seconds=10 - i))
        for i in range(5)
    ])
    # 全部 5 条按 ts DESC: 4, 3, 2, 1, 0
    resp = TestClient(app).get("/panel/admin/conversation-turns?page=2&page_size=2")
    body = resp.json()
    contents = [it["content"] for it in body["items"]]
    assert contents == ["2", "1"]
    assert body["total"] == 5


def test_list_role_filter(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", "u1", now - timedelta(minutes=3)),
        ("assistant", "a1", now - timedelta(minutes=2)),
        ("user", "u2", now - timedelta(minutes=1)),
    ])
    resp = TestClient(app).get("/panel/admin/conversation-turns?role=user")
    body = resp.json()
    assert body["total"] == 2
    assert [it["content"] for it in body["items"]] == ["u2", "u1"]


def test_list_source_filter_and_sources_endpoint(
    app: FastAPI, store: SqliteConversationStore
) -> None:
    """来源过滤 + /sources 列出去重列表."""
    now = datetime.now(timezone.utc)
    async def _seed_with_source() -> None:
        await store.append("user", "a", 1, source_frontend="astrbot",
                           ts=now - timedelta(minutes=3))
        await store.append("assistant", "b", 1, source_frontend="airi",
                           ts=now - timedelta(minutes=2))
        await store.append("user", "c", 1, source_frontend="astrbot",
                           ts=now - timedelta(minutes=1))
        await store.append("user", "d", 1, source_frontend=None,
                           ts=now)  # NULL 来源
    asyncio.get_event_loop().run_until_complete(_seed_with_source())

    # /sources 排除 NULL/空串, 去重, 按字典序
    resp = TestClient(app).get("/panel/admin/conversation-turns/sources")
    assert resp.status_code == 200
    assert resp.json() == {"items": ["airi", "astrbot"]}

    # source_frontend 参数精确匹配
    resp = TestClient(app).get(
        "/panel/admin/conversation-turns?source_frontend=astrbot"
    )
    body = resp.json()
    assert body["total"] == 2
    assert [it["content"] for it in body["items"]] == ["c", "a"]


def test_delete_single_turn_by_id(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", "keep", now - timedelta(minutes=2)),
        ("assistant", "remove", now - timedelta(minutes=1)),
    ])
    turns = asyncio.get_event_loop().run_until_complete(store.list_recent())
    remove_id = next(t.id for t in turns if t.content == "remove")
    resp = TestClient(app).delete(f"/panel/admin/conversation-turns/{remove_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    remaining = asyncio.get_event_loop().run_until_complete(store.list_recent())
    assert [t.content for t in remaining] == ["keep"]


def test_delete_single_turn_not_found(app: FastAPI) -> None:
    resp = TestClient(app).delete("/panel/admin/conversation-turns/999999")
    assert resp.status_code == 404


def test_delete_without_since_wipes_all(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", "a", now - timedelta(days=10)),
        ("assistant", "b", now - timedelta(hours=1)),
    ])
    resp = TestClient(app).delete("/panel/admin/conversation-turns")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
    assert asyncio.get_event_loop().run_until_complete(store.count()) == 0


def test_delete_with_since_only_wipes_older(app: FastAPI, store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    _seed(store, [
        ("user", "old", now - timedelta(days=10)),
        ("assistant", "recent", now - timedelta(hours=1)),
    ])
    cutoff = (now - timedelta(days=7)).isoformat()
    resp = TestClient(app).delete(
        "/panel/admin/conversation-turns", params={"since": cutoff}
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    remaining = asyncio.get_event_loop().run_until_complete(store.list_recent())
    assert len(remaining) == 1
    assert remaining[0].content == "recent"


def test_delete_with_invalid_since_returns_400(app: FastAPI) -> None:
    resp = TestClient(app).delete("/panel/admin/conversation-turns?since=not-a-date")
    assert resp.status_code == 400
    assert "invalid since" in resp.json()["detail"]
