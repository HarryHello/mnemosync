"""Reindex + Prune 单元测试.

覆盖 `should_prune` 各分支, Pruner dry_run/实删, Reindexer 全流程 (mocked embed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.core.memory.reindex import (
    Pruner,
    Reindexer,
    ReindexProgress,
    ReindexState,
    should_prune,
)
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.forwarder import Forwarder
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import LLMServiceProvider, ModelType
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _mk_entry(
    idx: int,
    *,
    memory_type: MemoryType = MemoryType.NORMAL,
    importance: float = 0.5,
    decay_rate: float = 0.1,
    priority: float = 0.5,
    is_forgotten: bool = False,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=f"m-{idx}",
        content=f"c-{idx}",
        role="user",
        source_user="alice",
        memory_type=memory_type,
        importance=importance,
        decay_rate=decay_rate,
        priority=priority,
        access_count=0,
        is_forgotten=is_forgotten,
        visibility=Visibility.SOURCE_RESTRICTED,
        emotional_tags=[],
        related_memories=[],
        created_at=created_at or NOW,
        last_accessed=None,
        expires_at=expires_at,
    )


# ─── should_prune ─────────────────────────────────────

def test_should_prune_permanent_never():
    e = _mk_entry(1, memory_type=MemoryType.PERMANENT, is_forgotten=True)
    drop, reason = should_prune(e, now=NOW)
    assert drop is False and reason == ""


def test_should_prune_forgotten():
    e = _mk_entry(2, is_forgotten=True)
    drop, reason = should_prune(e, now=NOW)
    assert drop is True and reason == "forgotten"


def test_should_prune_expired():
    e = _mk_entry(3, expires_at=NOW - timedelta(days=1))
    drop, reason = should_prune(e, now=NOW)
    assert drop is True and reason == "expired"


def test_should_prune_low_priority():
    # 半衰期 91 天(decay_rate=0.1), importance=0.1, 创建于 400 天前 → priority≈0.005
    e = _mk_entry(
        4, importance=0.1, decay_rate=0.1,
        created_at=NOW - timedelta(days=400),
    )
    drop, reason = should_prune(e, now=NOW)
    assert drop is True and reason == "low_priority"


def test_should_prune_active_kept():
    e = _mk_entry(
        5, importance=0.9, decay_rate=0.1,
        created_at=NOW - timedelta(days=1),
    )
    drop, reason = should_prune(e, now=NOW)
    assert drop is False and reason == ""


# ─── fixtures for stores ──────────────────────────────

@pytest.fixture
async def memory_store(tmp_path):
    ms = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await ms.connect()
    yield ms
    await ms.close()


@pytest.fixture
def vector_store(tmp_path):
    return VectorStore(str(tmp_path / "chroma"), collection_name="reindex_test")


@pytest.fixture
async def resolver_with_embedding(tmp_path):
    llm_store = LLMServiceStore(str(tmp_path / "llm.db"))
    await llm_store.init_db()
    await llm_store.save_service(
        LLMServiceProvider.create("svc-a", "https://a", "sk-a")
    )
    await llm_store.add_role_binding(
        ModelType.EMBEDDING, "svc-a", "embed-v3", embedding_dim=3
    )
    return RoleResolver(llm_store)


# ─── Pruner ───────────────────────────────────────────

async def test_pruner_dry_run(memory_store, vector_store):
    # 塞入: 1 permanent + 1 forgotten + 1 expired + 1 low + 1 active
    await memory_store.save(_mk_entry(1, memory_type=MemoryType.PERMANENT))
    await memory_store.save(_mk_entry(2, is_forgotten=True))
    await memory_store.save(_mk_entry(3, expires_at=NOW - timedelta(days=1)))
    await memory_store.save(_mk_entry(
        4, importance=0.05, decay_rate=0.1,
        created_at=NOW - timedelta(days=400),
    ))
    await memory_store.save(_mk_entry(
        5, importance=0.9, decay_rate=0.1,
        created_at=NOW - timedelta(days=1),
    ))

    pruner = Pruner(memory_store, vector_store)
    result = await pruner.run(priority_threshold=0.05, dry_run=True)

    assert result.total_before == 5
    assert result.would_delete == 3  # forgotten + expired + low_priority
    assert result.deleted == 0
    assert result.breakdown.forgotten == 1
    assert result.breakdown.expired == 1
    assert result.breakdown.low_priority == 1
    # 未删
    assert await memory_store.count_all() == 5


async def test_pruner_real_delete(memory_store, vector_store):
    await memory_store.save(_mk_entry(1, memory_type=MemoryType.PERMANENT))
    await memory_store.save(_mk_entry(2, is_forgotten=True))
    await memory_store.save(_mk_entry(3, expires_at=NOW - timedelta(days=1)))

    pruner = Pruner(memory_store, vector_store)
    result = await pruner.run(priority_threshold=0.05, dry_run=False)
    assert result.deleted == 2
    assert await memory_store.count_all() == 1  # 只剩 permanent


# ─── Reindexer ────────────────────────────────────────

async def test_reindexer_full_flow(memory_store, vector_store, resolver_with_embedding):
    # 塞入 3 条正常记忆 (使用 decay_rate=0.0 使 priority 恒为 importance, 避免创建时间影响)
    await memory_store.save(_mk_entry(1, importance=0.9, decay_rate=0.0))
    await memory_store.save(_mk_entry(2, memory_type=MemoryType.PERMANENT))
    await memory_store.save(_mk_entry(3, is_forgotten=True))  # 会被 prune

    forwarder = MultiForwarder(resolver_with_embedding)
    progress = ReindexProgress()
    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver_with_embedding, progress)

    with patch.object(
        Forwarder, "embed",
        new=AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
    ):
        await reindexer.run(prune=True, priority_threshold=0.05)

    assert progress.state == ReindexState.SUCCESS
    assert progress.total == 3
    assert progress.processed == 3
    assert progress.pruned == 1  # 只有 is_forgotten 被删
    assert progress.started_at is not None
    assert progress.finished_at is not None
    # 向量库锁已设
    lock = vector_store.get_embedding_lock()
    assert lock == {"service_id": "svc-a", "model": "embed-v3", "dim": 3}
    # 向量数 = 2 (permanent + normal, forgotten 已删)
    assert vector_store.count() == 2
    # sqlite 也删了 forgotten
    assert await memory_store.count_all() == 2


async def test_reindexer_without_prune_keeps_all(memory_store, vector_store, resolver_with_embedding):
    await memory_store.save(_mk_entry(1, is_forgotten=True))
    await memory_store.save(_mk_entry(2, memory_type=MemoryType.PERMANENT))

    forwarder = MultiForwarder(resolver_with_embedding)
    progress = ReindexProgress()
    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver_with_embedding, progress)

    with patch.object(
        Forwarder, "embed",
        new=AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
    ):
        await reindexer.run(prune=False)

    assert progress.pruned == 0
    assert vector_store.count() == 2
    assert await memory_store.count_all() == 2


async def test_reindexer_rejects_when_no_embedding_binding(tmp_path, memory_store, vector_store):
    llm_store = LLMServiceStore(str(tmp_path / "empty_llm.db"))
    await llm_store.init_db()
    resolver = RoleResolver(llm_store)

    forwarder = MultiForwarder(resolver)
    progress = ReindexProgress()
    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver, progress)

    with pytest.raises(Exception):
        await reindexer.run(prune=False)
    assert progress.state == ReindexState.ERROR
    assert progress.error is not None
