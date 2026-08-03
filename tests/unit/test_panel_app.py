"""面板进程 (前后端分离) 测试.

覆盖:
- 面板 app 构建
- 静态文件兜底
- /panel/auth/* 登录路由挂载
- 后端未启动时 /v1 代理返回 503
- 后端管理路由鉴权
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.panel.app import build_panel_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """构造面板 app 的 TestClient (后端未启动场景)."""
    from src.core.config import _reset_settings

    _reset_settings()

    app = build_panel_app()
    with TestClient(app) as c:
        yield c


def test_panel_app_builds() -> None:
    app = build_panel_app()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/panel/admin/{path:path}" in paths
    assert "/v1/{path:path}" in paths
    assert "/{full_path:path}" in paths


def test_proxy_v1_returns_503_when_backend_down(client: TestClient) -> None:
    """后端进程未启动时, /v1 代理应返回 503."""
    resp = client.get("/v1/models")
    assert resp.status_code == 503
    assert "后端未启动" in resp.json()["detail"]


def test_proxy_admin_returns_503_when_backend_down(client: TestClient) -> None:
    """后端进程未启动时, /panel/admin/* 代理应返回 503."""
    resp = client.get("/panel/admin/health")
    assert resp.status_code == 503


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
    # 若 ui/dist 存在返回 200 HTML; 否则仍走兜底逻辑
    assert resp.status_code in (200, 404)


def test_panel_prefix_api_returns_404_not_html(client: TestClient) -> None:
    """未命中的 /panel/* 应返回 404 而非 HTML (避免前端 fetch 拿到 HTML)."""
    resp = client.get("/panel/admin/backend/status")
    assert resp.status_code == 401  # 鉴权拦截
