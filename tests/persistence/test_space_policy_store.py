"""测试 SqliteSpacePolicyStore 的核心 CRUD.

覆盖: 策略保存/读取/删除, 全量列表, 空间隔离.
每个测试用独立 tmp 数据库, 无跨用例污染.
"""

from __future__ import annotations

import pytest
from src.persistence.space_policy_store import SpacePolicy, SqliteSpacePolicyStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteSpacePolicyStore(str(tmp_path / "policy.db"))
    await s.connect()
    yield s
    await s.close()


# ─── CRUD ──────────────────────────────────────────────


async def test_upsert_and_get_roundtrip(store):
    policy = SpacePolicy(
        space_id="room-1",
        expressor_enabled=False,
        expressor_temperature=0.8,
        preferred_max_length=500,
        use_emojis=False,
    )
    await store.upsert(policy)

    loaded = await store.get("room-1")
    assert loaded is not None
    assert loaded.space_id == "room-1"
    assert loaded.expressor_enabled is False
    assert loaded.expressor_temperature == 0.8
    assert loaded.preferred_max_length == 500
    assert loaded.use_emojis is False


async def test_get_nonexistent_returns_none(store):
    assert await store.get("no_such_space") is None


async def test_get_empty_string_returns_none(store):
    assert await store.get("") is None


async def test_delete_returns_true_when_hit(store):
    policy = SpacePolicy(space_id="to-delete")
    await store.upsert(policy)
    assert await store.delete("to-delete") is True
    assert await store.get("to-delete") is None


async def test_delete_returns_false_when_miss(store):
    assert await store.delete("nonexistent") is False


async def test_upsert_overwrites_on_same_space_id(store):
    p1 = SpacePolicy(space_id="s1", expressor_temperature=0.2)
    await store.upsert(p1)
    p2 = SpacePolicy(space_id="s1", expressor_temperature=0.9)
    await store.upsert(p2)

    loaded = await store.get("s1")
    assert loaded.expressor_temperature == 0.9


# ─── list_all ──────────────────────────────────────────


async def test_list_all_empty(store):
    assert await store.list_all() == []


async def test_list_all_returns_all(store):
    for i in range(3):
        await store.upsert(SpacePolicy(space_id=f"space-{i}"))

    items = await store.list_all()
    assert len(items) == 3
    ids = {p.space_id for p in items}
    assert ids == {"space-0", "space-1", "space-2"}


async def test_list_all_orders_by_space_id(store):
    await store.upsert(SpacePolicy(space_id="z-last"))
    await store.upsert(SpacePolicy(space_id="a-first"))
    await store.upsert(SpacePolicy(space_id="m-mid"))

    items = await store.list_all()
    assert [p.space_id for p in items] == ["a-first", "m-mid", "z-last"]


async def test_list_all_respects_limit(store):
    for i in range(5):
        await store.upsert(SpacePolicy(space_id=f"s{i}"))

    items = await store.list_all(limit=2)
    assert len(items) == 2


# ─── 边界 ──────────────────────────────────────────────


async def test_defaults_applied_when_missing(store):
    """get() 返回的默认值应与 SpacePolicy dataclass 一致."""
    # 直接插入一个原始行, 模拟旧数据
    import json
    async with store._conn() as db:
        await db.execute(
            "INSERT INTO space_policies (space_id, config, updated_at) VALUES (?, ?, ?)",
            ("legacy", "{}", "2025-01-01T00:00:00+00:00"),
        )
        await db.commit()

    loaded = await store.get("legacy")
    assert loaded.expressor_enabled is True
    assert loaded.expressor_temperature == 0.4
    assert loaded.preferred_max_length == 200
    assert loaded.use_emojis is True
