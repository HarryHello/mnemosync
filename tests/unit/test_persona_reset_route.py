"""Admin /persona/reset REST 路由测试.

覆盖:
- dry_run 只统计, 不删
- 非 dry_run: memory_entries (含 PERMANENT) / relationships / conversation_turns 全清
- 409: reindex running 时拒绝
- 部分失败: 某步抛异常, 错误进 errors, 其他步骤照常
- 重置后 get_relationship 返 None (确认没写默认行, 由后续对话自动补)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.core.memory.models import (
    MemoryEntry,
    MemoryType,
    Relationship,
    Visibility,
)
from src.core.memory.reindex import ReindexProgress, ReindexState
from src.persistence.auth_store import User
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.memory_store import SqliteMemoryStore
from src.infra.vector_store import VectorStore

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _mk_entry(idx: int, memory_type: MemoryType = MemoryType.NORMAL) -> MemoryEntry:
    return MemoryEntry(
        id=f"m-{idx}",
        content=f"c-{idx}",
        role="user",
        source_user="default",
        memory_type=memory_type,
        importance=0.9,
        decay_rate=0.0,
        priority=0.9,
        access_count=0,
        is_forgotten=False,
        visibility=Visibility.SOURCE_RESTRICTED,
        emotional_tags=[],
        related_memories=[],
        created_at=NOW,
        last_accessed=None,
        expires_at=None,
    )


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    memory_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    loop.run_until_complete(memory_store.connect())

    conversation_store = SqliteConversationStore(str(tmp_path / "conv.db"))
    loop.run_until_complete(conversation_store.connect())

    vector_store = VectorStore(str(tmp_path / "chroma"), collection_name="reset_test")
    progress = ReindexProgress()

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
        vector_store=vector_store,
        conversation_store=conversation_store,
        reindex_progress=progress,
    )
    app.dependency_overrides[get_current_user] = _fake_user

    # 预置数据: 1 条 PERMANENT + 2 条 NORMAL, 1 条 relationship, 3 条 conversation_turns
    loop.run_until_complete(memory_store.save(_mk_entry(1, MemoryType.PERMANENT)))
    loop.run_until_complete(memory_store.save(_mk_entry(2)))
    loop.run_until_complete(memory_store.save(_mk_entry(3)))
    loop.run_until_complete(
        memory_store.save_relationship(Relationship.create("default", "default"))
    )
    for i, role in enumerate(["user", "assistant", "user"]):
        loop.run_until_complete(
            conversation_store.append(
                role=role, content=f"turn-{i}", token_count=10, source_frontend="test"
            )
        )

    # 也写一条向量, 确认 reset_collection 会清掉
    vector_store.add(_mk_entry(1, MemoryType.PERMANENT), [0.1] * 8)

    yield app

    loop.run_until_complete(memory_store.close())
    loop.run_until_complete(conversation_store.close())
    loop.close()


def test_reset_dry_run_returns_counts_without_deletion(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/panel/admin/persona/reset", json={"dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["deleted_memories"] == 3
    assert body["deleted_relationships"] == 1
    assert body["deleted_conversation_turns"] == 3
    assert body["vector_reset"] is False
    assert body["errors"] == []

    # 计数应不变
    resp2 = client.post("/panel/admin/persona/reset", json={"dry_run": True})
    assert resp2.json()["deleted_memories"] == 3


def test_reset_wipes_all_including_permanent(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/panel/admin/persona/reset", json={"dry_run": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["deleted_memories"] == 3  # 含 PERMANENT
    assert body["deleted_relationships"] == 1
    assert body["deleted_conversation_turns"] == 3
    assert body["vector_reset"] is True
    assert body["errors"] == []

    # 后续状态: 全空
    import asyncio
    loop = asyncio.new_event_loop()
    memory_store: SqliteMemoryStore = app.state.memory_store
    conversation_store: SqliteConversationStore = app.state.conversation_store
    vector_store: VectorStore = app.state.vector_store
    try:
        assert loop.run_until_complete(memory_store.count_all()) == 0
        assert loop.run_until_complete(memory_store.count_relationships()) == 0
        assert loop.run_until_complete(conversation_store.count()) == 0
    finally:
        loop.close()
    assert vector_store.count() == 0


def test_reset_returns_409_during_reindex(app: FastAPI) -> None:
    client = TestClient(app)
    progress: ReindexProgress = app.state.reindex_progress
    progress.state = ReindexState.RUNNING
    try:
        resp = client.post("/panel/admin/persona/reset", json={"dry_run": False})
        assert resp.status_code == 409
    finally:
        progress.state = ReindexState.IDLE


def test_reset_partial_failure_reports_errors(app: FastAPI, monkeypatch) -> None:
    """conversation_store.delete_all 抛异常时, 前 3 步应完成, errors 非空."""
    client = TestClient(app)
    conversation_store: SqliteConversationStore = app.state.conversation_store

    async def boom() -> int:
        raise RuntimeError("simulated conv failure")

    monkeypatch.setattr(conversation_store, "delete_all", boom)

    resp = client.post("/panel/admin/persona/reset", json={"dry_run": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["vector_reset"] is True
    assert body["deleted_memories"] == 3
    assert body["deleted_relationships"] == 1
    assert body["deleted_conversation_turns"] == 0
    assert len(body["errors"]) == 1
    assert "conversation_turns" in body["errors"][0]


def test_reset_leaves_relationship_absent_for_lifecycle_to_recreate(app: FastAPI) -> None:
    """决策 4: 重置后 get_relationship 返 None, 由后续 lifecycle.update_relationship 自动补."""
    client = TestClient(app)
    resp = client.post("/panel/admin/persona/reset", json={"dry_run": False})
    assert resp.status_code == 200

    import asyncio
    loop = asyncio.new_event_loop()
    memory_store: SqliteMemoryStore = app.state.memory_store
    try:
        rel = loop.run_until_complete(memory_store.get_relationship("default", "default"))
    finally:
        loop.close()
    assert rel is None
