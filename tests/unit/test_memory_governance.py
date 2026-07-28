"""用户记忆治理端点测试."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
async def memory_store(tmp_path: Path):
    store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await store.connect()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_delete_by_user_all(memory_store: SqliteMemoryStore):
    """按用户删除全部记忆."""
    now = datetime.now(UTC)
    # 写入两条记忆
    from src.core.memory.models import MemoryEntry, MemoryType

    entry1 = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="喜欢咖啡",
        memory_type=MemoryType.PERMANENT,
        importance=0.8,
    )
    entry2 = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="喜欢茶",
        memory_type=MemoryType.NORMAL,
        importance=0.5,
    )
    entry3 = MemoryEntry.create(
        role="user",
        source_user="user-2",
        content="不相关",
        memory_type=MemoryType.NORMAL,
        importance=0.3,
    )
    await memory_store.save(entry1)
    await memory_store.save(entry2)
    await memory_store.save(entry3)

    # 删除 user-1 的所有记忆
    deleted = await memory_store.delete_by_user("user-1")
    assert deleted == 2

    # user-1 的记忆已删除
    remaining = await memory_store.list_page_for_user("user-1", limit=10, offset=0)
    assert remaining[0] == [] or len(remaining[0]) == 0
    # user-2 的记忆仍在
    remaining2 = await memory_store.list_page_for_user("user-2", limit=10, offset=0)
    assert len(remaining2[0]) == 1


@pytest.mark.asyncio
async def test_delete_by_user_with_type(memory_store: SqliteMemoryStore):
    """按用户和记忆类型删除."""
    from src.core.memory.models import MemoryEntry, MemoryType

    entry1 = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="永久记忆",
        memory_type=MemoryType.PERMANENT,
        importance=0.9,
    )
    entry2 = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="普通记忆",
        memory_type=MemoryType.NORMAL,
        importance=0.5,
    )
    await memory_store.save(entry1)
    await memory_store.save(entry2)

    # 只删除普通记忆
    deleted = await memory_store.delete_by_user("user-1", memory_type="normal")
    assert deleted == 1

    items, total = await memory_store.list_page_for_user("user-1", limit=10, offset=0)
    assert total == 1
    assert items[0].memory_type == MemoryType.PERMANENT


@pytest.mark.asyncio
async def test_delete_by_user_with_before(memory_store: SqliteMemoryStore):
    """按时间删除: 删除 before 之前创建的记忆."""
    from src.core.memory.models import MemoryEntry, MemoryType

    old = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="旧记忆",
        memory_type=MemoryType.NORMAL,
        importance=0.5,
    )
    old.created_at = datetime.now(UTC) - timedelta(days=30)
    new = MemoryEntry.create(
        role="user",
        source_user="user-1",
        content="新记忆",
        memory_type=MemoryType.NORMAL,
        importance=0.5,
    )
    await memory_store.save(old)
    await memory_store.save(new)

    cutoff = datetime.now(UTC) - timedelta(days=7)
    deleted = await memory_store.delete_by_user("user-1", before=cutoff)
    assert deleted == 1

    items, total = await memory_store.list_page_for_user("user-1", limit=10, offset=0)
    assert total == 1
    assert "新记忆" in items[0].content
