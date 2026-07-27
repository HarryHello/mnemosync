"""require_password_settled 依赖硬拦测试.

面板非 auth 路由被 include 时统一注入该 dep; must_change_password=True 一律 403
'password_change_required', 阻止 UI 被绕过后直接调管理接口.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import api_router
from src.api.routes.auth import get_current_user
from src.persistence.auth_store import User
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.memory_store import SqliteMemoryStore


def _user(must_change: bool) -> User:
    now = datetime.now(UTC)
    return User(
        id="test",
        username="test",
        password_hash="",
        must_change_password=must_change,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    memory_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    identity_store = SqliteIdentityStore(str(tmp_path / "identity.db"))
    loop.run_until_complete(memory_store.connect())
    loop.run_until_complete(identity_store.connect())

    app = FastAPI()
    app.include_router(api_router)
    app.state.memory_store = memory_store
    app.state.identity_store = identity_store

    yield app

    loop.run_until_complete(memory_store.close())
    loop.run_until_complete(identity_store.close())
    loop.close()


def test_must_change_password_blocks_admin_routes(app: FastAPI) -> None:
    """must_change_password=True → /panel/admin/* 返回 403 password_change_required."""
    app.dependency_overrides[get_current_user] = lambda: _user(must_change=True)

    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=default")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "password_change_required"


def test_settled_user_passes_dependency(app: FastAPI) -> None:
    """must_change_password=False → 依赖放行, /panel/admin/relationship 正常 200."""
    app.dependency_overrides[get_current_user] = lambda: _user(must_change=False)

    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=default")
    assert resp.status_code == 200, resp.text


def test_auth_me_not_blocked_by_dep(app: FastAPI) -> None:
    """/panel/auth/me 是白名单 — 即便 must_change_password=True 也能读到自己."""
    app.dependency_overrides[get_current_user] = lambda: _user(must_change=True)

    client = TestClient(app)
    resp = client.get("/panel/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["must_change_password"] is True
