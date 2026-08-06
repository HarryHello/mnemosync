"""Admin /admin 核心路由测试 (健康检查 + 仪表盘聚合).

覆盖:
  GET /admin/health   — 健康检查
  GET /admin/stats    — 仪表盘聚合 (api_keys / memories / logs / prompts / health)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.core.memory.models import MemoryEntry, MemoryType
from src.persistence.api_key_store import ApiKey, SqliteApiKeyStore
from src.persistence.auth_store import User
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    memory_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await memory_store.connect()
    api_key_store = SqliteApiKeyStore(str(tmp_path / "keys.db"))
    await api_key_store.connect()
    http_log_store = HttpLogStore(str(tmp_path / "logs.db"))
    await http_log_store.connect()

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.state = AppState(
        memory_store=memory_store,
        api_key_store=api_key_store,
        http_log_store=http_log_store,
    )
    app.dependency_overrides[get_current_user] = _fake_user

    yield app

    await memory_store.close()
    await api_key_store.close()
    await http_log_store.close()


def test_health_check(app: FastAPI) -> None:
    """健康检查返回 ok + 版本号 + 时间戳."""
    client = TestClient(app)
    resp = client.get("/panel/admin/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["timestamp"]


def test_stats_empty_counts(app: FastAPI) -> None:
    """空库 → 仪表盘计数为 0, 但 health 始终 ok."""
    client = TestClient(app)
    resp = client.get("/panel/admin/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_keys"] == 0
    assert body["memories"] == 0
    assert body["logs"] == 0
    assert body["health"]["status"] == "ok"


async def test_stats_reflects_seeded_data(app: FastAPI) -> None:
    """写入记忆/API Key/日志后, 仪表盘计数随之更新."""
    memory_store: SqliteMemoryStore = app.state.memory_store
    api_key_store: SqliteApiKeyStore = app.state.api_key_store
    http_log_store: HttpLogStore = app.state.http_log_store

    # 1 条记忆 (normal)
    await memory_store.save(
        MemoryEntry.create(
            content="记得买牛奶", role="user", source_user="alice",
            memory_type=MemoryType.NORMAL, importance=0.5,
        )
    )
    # 2 个 API Key (1 个 active, 1 个 inactive)
    active = ApiKey.generate("test-active")
    inactive = ApiKey.generate("test-inactive")
    inactive.is_active = False
    await api_key_store.save(active)
    await api_key_store.save(inactive)
    # 1 条 HTTP 日志
    http_log_store.enqueue({
        "method": "GET",
        "path": "/admin/health",
        "query_params": None,
        "request_headers": None,
        "request_body": None,
        "response_status": 200,
        "response_body": None,
        "duration_ms": 1.2,
        "client_ip": "127.0.0.1",
        "created_at": "2026-08-04T00:00:00Z",
    })
    await http_log_store.flush_sync()

    client = TestClient(app)
    resp = client.get("/panel/admin/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["memories"] == 1
    assert body["api_keys"] == 1  # 只计 active
    assert body["logs"] == 1
    assert body["prompts_total"] > 0  # 默认提示词目录非空
    assert body["prompts_overridden"] == 0  # 未配置 override
    assert body["health"]["status"] == "ok"


def test_stats_requires_auth(app: FastAPI) -> None:
    """未登录 → 401 (路由依赖 get_current_user)."""
    app.dependency_overrides.clear()
    client = TestClient(app)
    resp = client.get("/panel/admin/stats")
    assert resp.status_code == 401
