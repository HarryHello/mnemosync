"""面板后端管理路由测试."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.panel.app import build_panel_app


@pytest.fixture
def client(tmp_path: Path):
    """构造面板 app 的 TestClient."""
    from src.core.config import _reset_settings
    _reset_settings()
    app = build_panel_app()
    with TestClient(app) as c:
        yield c


def test_backend_status_requires_auth(client: TestClient):
    """后端状态端点需要认证."""
    resp = client.get("/panel/admin/backend/status")
    assert resp.status_code == 401


def test_backend_start_requires_auth(client: TestClient):
    """后端启动端点需要认证."""
    resp = client.post("/panel/admin/backend/start")
    assert resp.status_code == 401


def test_backend_stop_requires_auth(client: TestClient):
    """后端停止端点需要认证."""
    resp = client.post("/panel/admin/backend/stop")
    assert resp.status_code == 401


def test_backend_restart_requires_auth(client: TestClient):
    """后端重启端点需要认证."""
    resp = client.post("/panel/admin/backend/restart")
    assert resp.status_code == 401


def test_backend_status_returns_json(client: TestClient):
    """后端状态端点返回 JSON (需要 mock 认证)."""
    from src.api.routes.auth import get_current_user
    from src.persistence.auth_store import User
    from datetime import UTC, datetime

    def _fake_user():
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False, is_active=True,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )

    client.app.dependency_overrides[get_current_user] = _fake_user
    resp = client.get("/panel/admin/backend/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "running" in data
    assert "pid" in data
    assert "health" in data
    assert "port" in data
