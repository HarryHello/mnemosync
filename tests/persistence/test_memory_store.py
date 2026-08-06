"""测试 SqliteMemoryStore 的核心 CRUD.

覆盖: 记忆保存/读取/删除, 永久记忆过滤, 衰减评估更新, 关系状态读写.
每个测试用独立 tmp 数据库, 无跨用例污染.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.core.memory.models import (
    MemoryEntry,
    MemoryType,
    Relationship,
    Visibility,
)
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.relationship_store import SqliteRelationshipStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteMemoryStore(str(tmp_path / "memory.db"))
    await s.init_db()
    return s


@pytest.fixture
async def rel_store(store):
    """Relationship store sharing the same DB file as the memory store."""
    rs = SqliteRelationshipStore(store.db_path)
    await rs.init_db()
    return rs


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
    old.created_at = datetime.now(UTC) - timedelta(days=2)
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


async def test_relationship_roundtrip(rel_store):
    assert await rel_store.get_relationship("default", "alice") is None

    rel = Relationship.create("default", "alice")
    rel.apply_delta(intimacy_delta=0.15, trust_delta=0.10, new_type="friend",
                    notes="首次见面, 分享了一些个人信息")
    await rel_store.save_relationship(rel)

    loaded = await rel_store.get_relationship("default", "alice")
    assert loaded is not None
    assert loaded.intimacy_score == pytest.approx(0.15)
    assert loaded.trust_level == pytest.approx(0.10)
    assert loaded.interaction_count == 1
    assert loaded.type == "friend"
    assert "分享" in loaded.notes


async def test_relationship_upsert_overwrites(rel_store):
    rel = Relationship.create("default", "alice")
    await rel_store.save_relationship(rel)
    rel.apply_delta(intimacy_delta=0.5, trust_delta=0.5)
    await rel_store.save_relationship(rel)
    loaded = await rel_store.get_relationship("default", "alice")
    assert loaded.intimacy_score == pytest.approx(0.5)
    assert loaded.interaction_count == 1


# ─── 关系称呼动态演化 (v0.2.10) ─────────────────────────


async def test_update_addressing_writes_row_and_audit(rel_store):
    """首次调用: 建 relationship 行 + 写 audit 日志."""
    entries = await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="小哥",
        source="agent",
        reason="用户在当前消息中显式请求改称呼",
    )
    assert len(entries) == 1
    assert entries[0].field_name == "user_addressing"
    assert entries[0].old_value is None
    assert entries[0].new_value == "小哥"
    assert entries[0].source == "agent"

    rel = await rel_store.get_relationship("default", "alice")
    assert rel is not None
    assert rel.user_addressing == "小哥"
    assert rel.persona_addressing is None  # 未改的字段保持 NULL
    assert rel.context is None


async def test_update_addressing_two_fields_writes_two_audit_rows(rel_store):
    entries = await rel_store.update_relationship_addressing(
        "default", "alice",
        persona_addressing="人家",
        context="恋人",
        source="agent",
        reason="从兄妹演化到恋人关系",
    )
    assert len(entries) == 2
    fields = {e.field_name for e in entries}
    assert fields == {"persona_addressing", "context"}

    audit = await rel_store.list_relationship_audit("default", "alice")
    assert len(audit) == 2


async def test_update_addressing_no_change_skips_audit(rel_store):
    """相同值不写 audit 也不 UPDATE."""
    await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="哥",
        source="manual",
        reason="人工设置初始称呼",
    )
    entries = await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="哥",  # 与现值相同
        source="agent",
        reason="重复设置应被跳过",
    )
    assert entries == []
    audit = await rel_store.list_relationship_audit("default", "alice")
    assert len(audit) == 1  # 只有首次那条


async def test_update_addressing_all_none_raises(rel_store):
    with pytest.raises(ValueError):
        await rel_store.update_relationship_addressing(
            "default", "alice",
            source="agent",
            reason="没传字段应该报错",
        )


async def test_update_addressing_invalid_source_raises(rel_store):
    with pytest.raises(ValueError):
        await rel_store.update_relationship_addressing(
            "default", "alice",
            user_addressing="X",
            source="bogus",
            reason="source 必须是 agent 或 manual",
        )


async def test_list_audit_orders_by_id_desc(rel_store):
    await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="v1", source="agent", reason="first update xxxx",
    )
    await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="v2", source="manual", reason="second update xxxx",
    )
    audit = await rel_store.list_relationship_audit("default", "alice")
    assert len(audit) == 2
    assert audit[0].new_value == "v2"
    assert audit[1].new_value == "v1"


async def test_relationship_addressing_survives_save_relationship(rel_store):
    """save_relationship 后 addressing 列被序列化 (不被清空)."""
    await rel_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="小哥",
        source="agent", reason="设置初值",
    )
    rel = await rel_store.get_relationship("default", "alice")
    rel.apply_delta(intimacy_delta=0.2, trust_delta=0.1)
    await rel_store.save_relationship(rel)

    loaded = await rel_store.get_relationship("default", "alice")
    assert loaded.user_addressing == "小哥"
    assert loaded.intimacy_score == pytest.approx(0.2)
