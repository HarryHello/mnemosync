"""记忆纠正 (supersede) 测试."""

import asyncio
import pytest

from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
async def memory_store(tmp_path):
    s = SqliteMemoryStore(str(tmp_path / "memory.db"))
    await s.init_db()
    return s


@pytest.mark.asyncio
async def test_mark_superseded(memory_store):
    """标记替代后, 旧记忆 superseded_by 非空."""
    old = MemoryEntry.create(content="用户住在上海", role="user", source_user="u1")
    await memory_store.save(old)

    new = MemoryEntry.create(content="用户搬到北京了", role="user", source_user="u1")
    await memory_store.save(new)

    ok = await memory_store.mark_superseded(old.id, new.id)
    assert ok

    refreshed = await memory_store.get_by_id(old.id)
    assert refreshed.superseded_by == new.id


@pytest.mark.asyncio
async def test_mark_superseded_idempotent(memory_store):
    """已替代的记忆不能再次替代."""
    old = MemoryEntry.create(content="A", role="user", source_user="u1")
    await memory_store.save(old)
    new = MemoryEntry.create(content="B", role="user", source_user="u1")
    await memory_store.save(new)

    assert await memory_store.mark_superseded(old.id, new.id) is True
    # 第二次应失败 (已替代)
    assert await memory_store.mark_superseded(old.id, new.id) is False


@pytest.mark.asyncio
async def test_list_permanent_excludes_superseded(memory_store):
    """list_permanent 不返回已替代的记忆."""
    old = MemoryEntry.create(
        content="用户爱吃辣", role="user", source_user="u1",
        memory_type=MemoryType.PERMANENT,
    )
    old.visibility = Visibility.PUBLIC
    await memory_store.save(old)

    new = MemoryEntry.create(
        content="用户现在不吃辣了", role="user", source_user="u1",
        memory_type=MemoryType.PERMANENT,
    )
    new.visibility = Visibility.PUBLIC
    await memory_store.save(new)

    await memory_store.mark_superseded(old.id, new.id)

    perms = await memory_store.list_permanent("u1")
    ids = [p.id for p in perms]
    assert new.id in ids
    assert old.id not in ids


@pytest.mark.asyncio
async def test_list_page_excludes_superseded(memory_store):
    """list_page_for_user 不返回已替代的记忆."""
    old = MemoryEntry.create(content="旧信息", role="user", source_user="u1")
    await memory_store.save(old)
    new = MemoryEntry.create(content="新信息", role="user", source_user="u1")
    await memory_store.save(new)
    await memory_store.mark_superseded(old.id, new.id)

    items, total = await memory_store.list_page_for_user("u1", limit=10, offset=0)
    ids = [m.id for m in items]
    assert new.id in ids
    assert old.id not in ids
    assert total == 1  # 只有新记忆


@pytest.mark.asyncio
async def test_get_supersede_chain(memory_store):
    """替代链: A -> B -> C."""
    a = MemoryEntry.create(content="版本1", role="user", source_user="u1")
    await memory_store.save(a)
    b = MemoryEntry.create(content="版本2", role="user", source_user="u1")
    await memory_store.save(b)
    c = MemoryEntry.create(content="版本3", role="user", source_user="u1")
    await memory_store.save(c)

    await memory_store.mark_superseded(a.id, b.id)
    await memory_store.mark_superseded(b.id, c.id)

    chain = await memory_store.get_supersede_chain(a.id)
    assert len(chain) == 3
    assert chain[0].id == a.id
    assert chain[1].id == b.id
    assert chain[2].id == c.id
    assert chain[2].superseded_by is None  # 最新版本未被替代
