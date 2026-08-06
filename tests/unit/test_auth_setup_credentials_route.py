"""POST /panel/auth/setup-credentials 路由行为测试.

首次登录 (must_change_password=True) 时一次性设定新用户名 + 新密码;
完成后 must_change_password=False, 走该端点将被拒绝, 改走 /auth/change-password.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api import api_router
from src.api.state import AppState
from src.persistence.auth_store import SqliteAuthStore


@pytest.fixture
async def app_and_store(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, SqliteAuthStore]]:
    auth_store = SqliteAuthStore(str(tmp_path / "auth.db"))
    await auth_store.connect()
    await auth_store.create_default_user("mnemosync")

    app = FastAPI()
    app.include_router(api_router)
    app.state = AppState(auth_store=auth_store)

    yield app, auth_store

    await auth_store.close()


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post(
        "/panel/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_setup_credentials_success_flips_must_change(
    app_and_store: tuple[FastAPI, SqliteAuthStore],
) -> None:
    """must_change_password=True 时, 成功修改并翻位到 False."""
    app, store = app_and_store
    client = TestClient(app)
    token = _login(client, "mnemosync", "mnemosync")

    resp = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "old_password": "mnemosync",
            "new_username": "harry",
            "new_password": "newpass123",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    user = await store.get_user_by_username("harry")
    assert user is not None
    assert user.must_change_password is False


def test_setup_credentials_rejected_after_settled(
    app_and_store: tuple[FastAPI, SqliteAuthStore],
) -> None:
    """must_change_password=False 时再调, 400 引导用 change-password."""
    app, _store = app_and_store
    client = TestClient(app)
    token = _login(client, "mnemosync", "mnemosync")

    ok = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "old_password": "mnemosync",
            "new_username": "harry",
            "new_password": "newpass123",
        },
    )
    assert ok.status_code == 200

    token2 = _login(client, "harry", "newpass123")
    resp = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token2}"},
        json={
            "old_password": "newpass123",
            "new_username": "harry2",
            "new_password": "newpass456",
        },
    )
    assert resp.status_code == 400
    assert "初始化" in resp.json()["detail"]


def test_setup_credentials_wrong_old_password(
    app_and_store: tuple[FastAPI, SqliteAuthStore],
) -> None:
    """原密码错误 → 400."""
    app, _store = app_and_store
    client = TestClient(app)
    token = _login(client, "mnemosync", "mnemosync")

    resp = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "old_password": "wrong",
            "new_username": "harry",
            "new_password": "newpass123",
        },
    )
    assert resp.status_code == 400
    assert "原密码" in resp.json()["detail"]


def test_setup_credentials_weak_password(
    app_and_store: tuple[FastAPI, SqliteAuthStore],
) -> None:
    """新密码 < 6 位, Pydantic min_length 拦 422 (schema 层)."""
    app, _store = app_and_store
    client = TestClient(app)
    token = _login(client, "mnemosync", "mnemosync")

    resp = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "old_password": "mnemosync",
            "new_username": "harry",
            "new_password": "1234",
        },
    )
    assert resp.status_code == 422


def test_setup_credentials_default_password_rejected(
    app_and_store: tuple[FastAPI, SqliteAuthStore],
) -> None:
    """不允许用回默认密码 mnemosync (validate_password_strength 拦)."""
    app, _store = app_and_store
    client = TestClient(app)
    token = _login(client, "mnemosync", "mnemosync")

    resp = client.post(
        "/panel/auth/setup-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "old_password": "mnemosync",
            "new_username": "harry",
            "new_password": "mnemosync",
        },
    )
    assert resp.status_code == 400
    assert "默认" in resp.json()["detail"]
