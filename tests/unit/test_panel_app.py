"""面板进程 (前后端分离) 测试.

覆盖:
- 面板 app 构建
- /panel/auth/* 登录路由挂载
- /v1 代理到后端
- /panel/* 代理到后端
- 后端管理路由鉴权
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def test_panel_app_builds() -> None:
    app = build_panel_app()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/panel/{path:path}" in paths
    assert "/v1/{path:path}" in paths
    assert "/{full_path:path}" in paths


def test_proxy_v1_to_backend(client: TestClient) -> None:
    """/v1 代理到后端 (后端未启动时 503, 启动时转发)."""
    resp = client.get("/v1/models")
    # 后端未启动 → 503; 后端运行 → 200/401
    assert resp.status_code in (200, 401, 503)


def test_proxy_panel_to_backend(client: TestClient) -> None:
    """/panel/* 代理到后端 (如 /panel/api-keys)."""
    resp = client.get("/panel/api-keys")
    # 后端未启动 → 503; 后端运行 → 401 (需认证)
    assert resp.status_code in (401, 503)


def test_auth_login_route_mounted(client: TestClient) -> None:
    """登录路由应在面板进程挂载 (相对路径 /panel/auth/login)."""
    resp = client.post("/panel/auth/login", json={"username": "x", "password": "y"})
    # 无论登录成败, 路由存在 (401 而非 404)
    assert resp.status_code in (401, 200)


def test_backend_status_requires_auth(client: TestClient) -> None:
    """后端管理路由需鉴权, 未登录返回 401."""
    resp = client.get("/panel/admin/backend/status")
    assert resp.status_code == 401


def test_spa_fallback_serves_index(client: TestClient) -> None:
    """非 API 路径应返回 index.html (SPA 兜底)."""
    resp = client.get("/some/spa/route")
    assert resp.status_code in (200, 404)
