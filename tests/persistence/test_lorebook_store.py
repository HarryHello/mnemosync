"""测试 SqliteLorebookStore 的核心 CRUD.

覆盖: Lorebook 保存/读取/删除, 分页列表, 计数, 关键词匹配.
每个测试用独立 tmp 数据库, 无跨用例污染.
"""

from __future__ import annotations

import pytest
from src.persistence.lorebook_store import LorebookEntry, SqliteLorebookStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteLorebookStore(str(tmp_path / "lorebook.db"))
    await s.connect()
    yield s
    await s.close()


# ─── CRUD ──────────────────────────────────────────────


async def test_save_and_get_roundtrip(store):
    entry = LorebookEntry.create(
        content="魔法世界设定: 大陆分为东西两块",
        keywords=["魔法", "大陆", "世界设定"],
        priority=5,
        space_id="fantasy",
    )
    await store.save(entry)

    loaded = await store.get_by_id(entry.id)
    assert loaded is not None
    assert loaded.content == "魔法世界设定: 大陆分为东西两块"
    assert loaded.keywords == ["魔法", "大陆", "世界设定"]
    assert loaded.priority == 5
    assert loaded.space_id == "fantasy"


async def test_get_by_id_missing_returns_none(store):
    assert await store.get_by_id("nonexistent") is None


async def test_delete_returns_true_when_hit(store):
    entry = LorebookEntry.create(content="tmp", keywords=["kw"])
    await store.save(entry)
    assert await store.delete(entry.id) is True
    assert await store.get_by_id(entry.id) is None


async def test_delete_returns_false_when_miss(store):
    assert await store.delete("nonexistent") is False


async def test_save_upserts_on_same_id(store):
    entry = LorebookEntry.create(content="v1", keywords=["a"])
    await store.save(entry)
    entry.content = "v2"
    await store.save(entry)
    loaded = await store.get_by_id(entry.id)
    assert loaded is not None
    assert loaded.content == "v2"


# ─── list_page ─────────────────────────────────────────


async def test_list_page_basic(store):
    for i in range(5):
        e = LorebookEntry.create(content=f"entry-{i}", keywords=["kw"], priority=i)
        await store.save(e)

    items, total = await store.list_page(limit=3, offset=0)
    assert total == 5
    assert len(items) == 3


async def test_list_page_offset(store):
    for i in range(5):
        e = LorebookEntry.create(content=f"entry-{i}", keywords=["kw"])
        await store.save(e)

    items, total = await store.list_page(limit=2, offset=4)
    assert total == 5
    assert len(items) == 1


async def test_list_page_filter_by_space_id(store):
    a = LorebookEntry.create(content="a", keywords=["kw"], space_id="s1")
    b = LorebookEntry.create(content="b", keywords=["kw"], space_id="s2")
    c = LorebookEntry.create(content="c", keywords=["kw"], space_id=None)
    await store.save(a)
    await store.save(b)
    await store.save(c)

    items, total = await store.list_page(limit=10, offset=0, space_id="s1")
    assert total == 1
    assert items[0].id == a.id


async def test_list_page_sort_by_priority(store):
    low = LorebookEntry.create(content="low", keywords=["kw"], priority=1)
    high = LorebookEntry.create(content="high", keywords=["kw"], priority=10)
    mid = LorebookEntry.create(content="mid", keywords=["kw"], priority=5)
    await store.save(low)
    await store.save(high)
    await store.save(mid)

    items, _ = await store.list_page(limit=10, offset=0, sort_by="priority", sort_order="desc")
    assert [e.priority for e in items] == [10, 5, 1]


async def test_list_page_empty(store):
    items, total = await store.list_page(limit=10, offset=0)
    assert items == []
    assert total == 0


# ─── count ─────────────────────────────────────────────


async def test_count(store):
    assert await store.count() == 0
    for _ in range(3):
        e = LorebookEntry.create(content="x", keywords=["kw"])
        await store.save(e)
    assert await store.count() == 3


# ─── match_for_space ───────────────────────────────────


async def test_match_for_space_basic(store):
    e1 = LorebookEntry.create(
        content="龙族设定", keywords=["dragon", "龙"], priority=10, space_id="s1"
    )
    e2 = LorebookEntry.create(
        content="精灵设定", keywords=["elf", "精灵"], priority=5, space_id="s1"
    )
    await store.save(e1)
    await store.save(e2)

    matched = await store.match_for_space("传说中的dragon很强大", space_id="s1")
    assert len(matched) == 1
    assert matched[0].id == e1.id


async def test_match_for_space_case_insensitive(store):
    e = LorebookEntry.create(
        content="tech", keywords=["Python"], priority=1, space_id="s1"
    )
    await store.save(e)

    matched = await store.match_for_space("I love python programming", space_id="s1")
    assert len(matched) == 1


async def test_match_for_space_includes_null_space(store):
    """space_id IS NULL 的条目应被匹配 (全局条目)."""
    e = LorebookEntry.create(
        content="通用规则", keywords=["rule"], priority=1, space_id=None
    )
    await store.save(e)

    matched = await store.match_for_space("请遵守 rule", space_id="any_space")
    assert len(matched) == 1


async def test_match_for_space_empty_text_returns_empty(store):
    e = LorebookEntry.create(content="x", keywords=["kw"], space_id="s1")
    await store.save(e)
    assert await store.match_for_space("", space_id="s1") == []


async def test_match_for_space_no_match(store):
    e = LorebookEntry.create(
        content="精灵", keywords=["elf"], priority=1, space_id="s1"
    )
    await store.save(e)
    matched = await store.match_for_space("dragon world", space_id="s1")
    assert matched == []


async def test_match_for_space_sorted_by_priority(store):
    low = LorebookEntry.create(content="low", keywords=["kw"], priority=1, space_id="s1")
    high = LorebookEntry.create(content="high", keywords=["kw"], priority=10, space_id="s1")
    await store.save(low)
    await store.save(high)

    matched = await store.match_for_space("test kw text", space_id="s1")
    assert len(matched) == 2
    assert matched[0].priority >= matched[1].priority


async def test_match_for_space_respects_limit(store):
    for i in range(10):
        e = LorebookEntry.create(
            content=f"entry-{i}", keywords=["common"], priority=i, space_id="s1"
        )
        await store.save(e)

    matched = await store.match_for_space("common topic", space_id="s1", limit=3)
    assert len(matched) == 3


async def test_match_for_space_empty_keywords_not_matched(store):
    e = LorebookEntry.create(content="no keywords", keywords=[], space_id="s1")
    await store.save(e)
    matched = await store.match_for_space("any text", space_id="s1")
    assert matched == []


# ─── 边界 ──────────────────────────────────────────────


async def test_delete_nonexistent_returns_false(store):
    assert await store.delete("does_not_exist") is False


async def test_get_by_id_after_delete_returns_none(store):
    entry = LorebookEntry.create(content="to delete", keywords=["x"])
    await store.save(entry)
    await store.delete(entry.id)
    assert await store.get_by_id(entry.id) is None
