"""SqliteRelationshipStore 关系 CRUD 测试.

覆盖: get / save / update / delete (delete_all_relationships)、count、
list_relationships 分页与排序、关系动态称呼演化 (审计日志)。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.core.memory.models import Relationship


async def test_get_relationship_missing_returns_none(relationship_store) -> None:
    """空表查询 → None."""
    assert await relationship_store.get_relationship("default", "alice") is None


async def test_save_then_get_roundtrip(relationship_store) -> None:
    """save 后 get 能取回同一份关系."""
    rel = Relationship.create("default", "alice")
    rel.intimacy_score = 0.42
    rel.trust_level = 0.31
    rel.type = "friend"
    rel.interaction_count = 7
    rel.last_active = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
    rel.notes = "关系不错"
    rel.persona_addressing = "小爱"
    rel.user_addressing = "主人"
    rel.context = "一起打游戏"
    await relationship_store.save_relationship(rel)

    got = await relationship_store.get_relationship("default", "alice")
    assert got is not None
    assert got.persona_id == "default"
    assert got.user_id == "alice"
    assert got.intimacy_score == pytest.approx(0.42)
    assert got.trust_level == pytest.approx(0.31)
    assert got.type == "friend"
    assert got.interaction_count == 7
    assert got.notes == "关系不错"
    assert got.persona_addressing == "小爱"
    assert got.user_addressing == "主人"
    assert got.context == "一起打游戏"


async def test_save_upsert_updates_existing(relationship_store) -> None:
    """多次 save 同一 (persona, user) → 覆盖旧值, 不产生重复行."""
    rel = Relationship.create("default", "alice")
    rel.intimacy_score = 0.1
    await relationship_store.save_relationship(rel)

    rel.intimacy_score = 0.9
    rel.type = "intimate"
    await relationship_store.save_relationship(rel)

    got = await relationship_store.get_relationship("default", "alice")
    assert got.intimacy_score == pytest.approx(0.9)
    assert got.type == "intimate"
    assert await relationship_store.count_relationships() == 1


async def test_delete_all_relationships(relationship_store) -> None:
    """delete_all_relationships 清空所有关系, 返回删除行数."""
    for uid in ("alice", "bob", "carol"):
        await relationship_store.save_relationship(Relationship.create("default", uid))
    assert await relationship_store.count_relationships() == 3

    deleted = await relationship_store.delete_all_relationships()
    assert deleted == 3
    assert await relationship_store.count_relationships() == 0
    assert await relationship_store.get_relationship("default", "alice") is None


async def test_count_relationships(relationship_store) -> None:
    """空表 count=0, 写入后 count 正确."""
    assert await relationship_store.count_relationships() == 0
    await relationship_store.save_relationship(Relationship.create("default", "u1"))
    await relationship_store.save_relationship(Relationship.create("default", "u2"))
    assert await relationship_store.count_relationships() == 2


async def test_list_relationships_sorted_by_intimacy_desc(relationship_store) -> None:
    """默认按亲密度降序返回."""
    for uid, intimacy in [("alice", 0.3), ("bob", 0.9), ("carol", 0.1)]:
        rel = Relationship.create("default", uid)
        rel.intimacy_score = intimacy
        await relationship_store.save_relationship(rel)

    items, total = await relationship_store.list_relationships("default")
    assert total == 3
    assert [r.user_id for r in items] == ["bob", "alice", "carol"]


async def test_list_relationships_only_named_persona(relationship_store) -> None:
    """list_relationships 只返回指定 persona 的关系."""
    await relationship_store.save_relationship(Relationship.create("persona_a", "alice"))
    await relationship_store.save_relationship(Relationship.create("persona_b", "bob"))
    items, total = await relationship_store.list_relationships("persona_a")
    assert total == 1
    assert items[0].user_id == "alice"


async def test_update_relationship_addressing_writes_audit(relationship_store) -> None:
    """称呼演化: 更新字段同时写审计日志, 返回对应条目."""
    entries = await relationship_store.update_relationship_addressing(
        "default", "alice",
        user_addressing="主人",
        source="manual",
        reason="人工设置称呼",
    )
    assert len(entries) == 1
    assert entries[0].field_name == "user_addressing"
    assert entries[0].new_value == "主人"
    assert entries[0].old_value is None
    assert entries[0].source == "manual"

    got = await relationship_store.get_relationship("default", "alice")
    assert got.user_addressing == "主人"

    audit = await relationship_store.list_relationship_audit("default", "alice")
    assert len(audit) == 1
    assert audit[0].field_name == "user_addressing"


async def test_update_relationship_addressing_rejects_bad_source(relationship_store) -> None:
    """非法 source → ValueError."""
    with pytest.raises(ValueError):
        await relationship_store.update_relationship_addressing(
            "default", "alice", user_addressing="x", source="evil", reason="test"
        )


async def test_update_relationship_addressing_rejects_all_none(relationship_store) -> None:
    """无任何待更新字段 → ValueError."""
    with pytest.raises(ValueError):
        await relationship_store.update_relationship_addressing(
            "default", "alice", source="manual", reason="test"
        )
