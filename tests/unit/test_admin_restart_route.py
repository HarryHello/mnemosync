"""服务重启路由测试.

覆盖:
- 未登录 → 401
- 已登录 → 200, 返回 success + message, 并触发 restart 子进程
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin_restart import router as admin_restart_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import User


@pytest.fixture
async def api_key_store(tmp_path: Path) -> SqliteApiKeyStore:
    s = SqliteApiKeyStore(str(tmp_path / "k.db"))
    await s.connect()
    yield s
    await s.close()


@pytest.fixture
def app(api_key_store: SqliteApiKeyStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    admin = APIRouter(prefix="/admin")
    admin.include_router(admin_restart_router)
    outer.include_router(admin)
    app.include_router(outer)
    app.state = AppState(api_key_store=api_key_store)

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
    admin = APIRouter(prefix="/admin")
    admin.include_router(admin_restart_router)
    outer.include_router(admin)
    app.include_router(outer)
    app.state = AppState(api_key_store=api_key_store)
    return app


def test_restart_requires_auth(app_unauth: FastAPI) -> None:
    client = TestClient(app_unauth)
    assert client.post("/panel/admin/restart").status_code == 401


def test_restart_triggers_subprocess_and_returns_before_completion(app: FastAPI) -> None:
    mock_proc = Mock()
    mock_proc.pid = 12345

    with patch("src.api.routes.admin_restart.subprocess.Popen", return_value=mock_proc) as popen:
        client = TestClient(app)
        resp = client.post("/panel/admin/restart")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "12345" in data["message"]

    popen.assert_called_once()
    kwargs = popen.call_args.kwargs
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] is not None
    # 命令应为 python -m src.cli.cli restart
    cmd = popen.call_args.args[0]
    assert cmd[-3:] == ["-m", "src.cli.cli", "restart"]
