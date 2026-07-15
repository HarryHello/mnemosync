"""测试 SqliteMemoryStore 的核心 CRUD.

覆盖: 记忆保存/读取/删除, 永久记忆过滤, 衰减评估更新, 关系状态读写.
每个测试用独立 tmp 数据库, 无跨用例污染.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.memory.models import (
    MemoryEntry,
    MemoryType,
    Relationship,
    Visibility,
)
from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteMemoryStore(str(tmp_path / "memory.db"))
    await s.init_db()
    return s


# ─── 记忆 CRUD ────────────────────────────────────────────


async def test_save_and_get_roundtrip(store):
    entry = MemoryEntry.create(
        content="用户对花生过敏",
        role="user",
        source_user="alice",
        memory_type=MemoryType.PERMANENT,
        importance=1.0,
        decay_rate=0.0,
    )
    entry.emotional_tags = ["health", "allergy"]
    await store.save(entry)

    loaded = await store.get_by_id(entry.id)
    assert loaded is not None
    assert loaded.content == "用户对花生过敏"
    assert loaded.memory_type == MemoryType.PERMANENT
    assert loaded.importance == 1.0
    assert loaded.source_user == "alice"
    assert loaded.emotional_tags == ["health", "allergy"]
    assert loaded.visibility == Visibility.SOURCE_RESTRICTED


async def test_get_by_id_missing_returns_none(store):
    assert await store.get_by_id("nonexistent") is None


async def test_delete_returns_true_when_hit(store):
    entry = MemoryEntry.create(content="tmp", role="user", source_user="u")
    await store.save(entry)
    assert await store.delete(entry.id) is True
    assert await store.get_by_id(entry.id) is None


async def test_delete_returns_false_when_miss(store):
    assert await store.delete("nonexistent") is False


# ─── 永久记忆过滤 ────────────────────────────────────────


async def test_list_permanent_filters_by_type_and_forgotten(store):
    permanent = MemoryEntry.create(
        content="permanent",
        role="user",
        source_user="alice",
        memory_type=MemoryType.PERMANENT,
        importance=0.9,
    )
    normal = MemoryEntry.create(
        content="normal",
        role="user",
        source_user="alice",
        memory_type=MemoryType.NORMAL,
    )
    forgotten = MemoryEntry.create(
        content="forgotten",
        role="user",
        source_user="alice",
        memory_type=MemoryType.PERMANENT,
    )
    forgotten.mark_forgotten()

    for e in (permanent, normal, forgotten):
        await store.save(e)

    result = await store.list_permanent("alice", limit=10)
    assert len(result) == 1
    assert result[0].id == permanent.id


async def test_list_permanent_isolates_by_source_user(store):
    """单人格单用户是当前定位, 但 source_user 层的隔离仍应工作 (默认桶就叫 default)."""
    a = MemoryEntry.create(content="a-mem", role="user", source_user="alice",
                            memory_type=MemoryType.PERMANENT)
    b = MemoryEntry.create(content="b-mem", role="user", source_user="bob",
                            memory_type=MemoryType.PERMANENT)
    await store.save(a)
    await store.save(b)

    alice = await store.list_permanent("alice", limit=10)
    bob = await store.list_permanent("bob", limit=10)
    assert [e.content for e in alice] == ["a-mem"]
    assert [e.content for e in bob] == ["b-mem"]


async def test_list_permanent_orders_by_importance(store):
    high = MemoryEntry.create(
        content="high", role="user", source_user="u",
        memory_type=MemoryType.PERMANENT, importance=0.9,
    )
    low = MemoryEntry.create(
        content="low", role="user", source_user="u",
        memory_type=MemoryType.PERMANENT, importance=0.3,
    )
    await store.save(low)
    await store.save(high)
    result = await store.list_permanent("u", limit=10)
    assert [e.content for e in result] == ["high", "low"]


# ─── 优先级更新 & 访问标记 ───────────────────────────────


async def test_update_priority_and_forgotten(store):
    entry = MemoryEntry.create(content="x", role="user", source_user="u")
    await store.save(entry)

    await store.update_priority(entry.id, priority=0.02, is_forgotten=True)
    loaded = await store.get_by_id(entry.id)
    assert loaded.priority == 0.02
    assert loaded.is_forgotten is True


async def test_mark_accessed_increments_and_sets_time(store):
    entry = MemoryEntry.create(content="x", role="user", source_user="u")
    await store.save(entry)

    await store.mark_accessed(entry.id)
    await store.mark_accessed(entry.id)
    loaded = await store.get_by_id(entry.id)
    assert loaded.access_count == 2
    assert loaded.last_accessed is not None


async def test_list_for_decay_skips_recent(store):
    """< skip_hours 的记忆不出现在衰减候选中."""
    fresh = MemoryEntry.create(content="fresh", role="user", source_user="u")
    await store.save(fresh)

    result = await store.list_for_decay(skip_hours=24, limit=10)
    assert all(e.id != fresh.id for e in result)


async def test_list_for_decay_includes_old(store):
    old = MemoryEntry.create(content="old", role="user", source_user="u")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    await store.save(old)

    result = await store.list_for_decay(skip_hours=24, limit=10)
    assert any(e.id == old.id for e in result)


async def test_count_permanent(store):
    for _ in range(3):
        e = MemoryEntry.create(
            content="p", role="user", source_user="u",
            memory_type=MemoryType.PERMANENT,
        )
        await store.save(e)
    normal = MemoryEntry.create(content="n", role="user", source_user="u")
    await store.save(normal)

    assert await store.count_permanent("u") == 3


# ─── 关系状态 ────────────────────────────────────────────


async def test_relationship_roundtrip(store):
    assert await store.get_relationship("default", "alice") is None

    rel = Relationship.create("default", "alice")
    rel.apply_delta(intimacy_delta=0.15, trust_delta=0.10, new_type="friend",
                    notes="首次见面, 分享了一些个人信息")
    await store.save_relationship(rel)

    loaded = await store.get_relationship("default", "alice")
    assert loaded is not None
    assert loaded.intimacy_score == pytest.approx(0.15)
    assert loaded.trust_level == pytest.approx(0.10)
    assert loaded.interaction_count == 1
    assert loaded.type == "friend"
    assert "分享" in loaded.notes


async def test_relationship_upsert_overwrites(store):
    rel = Relationship.create("default", "alice")
    await store.save_relationship(rel)
    rel.apply_delta(intimacy_delta=0.5, trust_delta=0.5)
    await store.save_relationship(rel)
    loaded = await store.get_relationship("default", "alice")
    assert loaded.intimacy_score == pytest.approx(0.5)
    assert loaded.interaction_count == 1
