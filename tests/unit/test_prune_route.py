"""Admin /memory/prune + /memory/reindex/status REST 路由测试.

Reindex 是异步背景任务, 完整测试太重, 这里只覆盖:
- 鉴权
- Prune dry_run / 实删
- Reindex status idle 初始态
- 409: reindex 已在运行时 prune / start-reindex 返 409
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.core.memory.reindex import ReindexProgress, ReindexState
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.auth_store import User
from src.persistence.memory_store import SqliteMemoryStore

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _mk_entry(
    idx: int,
    *,
    memory_type: MemoryType = MemoryType.NORMAL,
    is_forgotten: bool = False,
    expires_at: datetime | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=f"m-{idx}",
        content=f"c-{idx}",
        role="user",
        source_user="alice",
        memory_type=memory_type,
        importance=0.9,
        decay_rate=0.0,
        priority=0.9,
        access_count=0,
        is_forgotten=is_forgotten,
        visibility=Visibility.SOURCE_RESTRICTED,
        emotional_tags=[],
        related_memories=[],
        created_at=NOW,
        last_accessed=None,
        expires_at=expires_at,
    )


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    memory_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    loop.run_until_complete(memory_store.connect())

    llm_store = LLMServiceStore(str(tmp_path / "llm.db"))
    loop.run_until_complete(llm_store.init_db())

    vector_store = VectorStore(str(tmp_path / "chroma"), collection_name="route_test")
    resolver = RoleResolver(llm_store)
    forwarder = MultiForwarder(resolver)
    progress = ReindexProgress()

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            is_active=True, created_at=None, updated_at=None,
        )

    app.state.llm_service_store = llm_store
    app.state.memory_store = memory_store
    app.state.vector_store = vector_store
    app.state.resolver = resolver
    app.state.multi_forwarder = forwarder
    app.state.reindex_progress = progress
    app.dependency_overrides[get_current_user] = _fake_user

    # 预置记忆
    loop.run_until_complete(memory_store.save(_mk_entry(1, memory_type=MemoryType.PERMANENT)))
    loop.run_until_complete(memory_store.save(_mk_entry(2, is_forgotten=True)))
    loop.run_until_complete(memory_store.save(_mk_entry(3, expires_at=NOW - timedelta(days=1))))

    yield app

    loop.run_until_complete(memory_store.close())
    loop.close()


def test_reindex_status_idle_initial(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/panel/admin/memory/reindex/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"
    assert body["total"] == 0
    assert body["processed"] == 0


def test_prune_dry_run_returns_breakdown_without_deleting(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/memory/prune",
        json={"priority_threshold": 0.05, "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_before"] == 3
    assert body["would_delete"] == 2  # forgotten + expired
    assert body["deleted"] == 0
    assert body["breakdown"] == {"forgotten": 1, "expired": 1, "low_priority": 0}

    # 数据未变
    resp = client.post(
        "/panel/admin/memory/prune",
        json={"priority_threshold": 0.05, "dry_run": True},
    )
    assert resp.json()["total_before"] == 3


def test_prune_actual_delete(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/memory/prune",
        json={"priority_threshold": 0.05, "dry_run": False},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2

    # 再跑一次 dry_run, 剩 1
    resp = client.post(
        "/panel/admin/memory/prune",
        json={"priority_threshold": 0.05, "dry_run": True},
    )
    assert resp.json()["total_before"] == 1
    assert resp.json()["would_delete"] == 0


def test_prune_blocked_while_reindex_running(app: FastAPI) -> None:
    client = TestClient(app)
    # 手工把 progress 置为 running
    progress: ReindexProgress = app.state.reindex_progress
    progress.state = ReindexState.RUNNING
    try:
        resp = client.post(
            "/panel/admin/memory/prune",
            json={"priority_threshold": 0.05, "dry_run": True},
        )
        assert resp.status_code == 409
    finally:
        progress.state = ReindexState.IDLE


def test_reindex_start_returns_409_when_running(app: FastAPI) -> None:
    client = TestClient(app)
    progress: ReindexProgress = app.state.reindex_progress
    progress.state = ReindexState.RUNNING
    try:
        resp = client.post(
            "/panel/admin/memory/reindex",
            json={"prune": False},
        )
        assert resp.status_code == 409
    finally:
        progress.state = ReindexState.IDLE
