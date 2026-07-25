"""memory_store 空间字段持久化 (v0.3.0 Sub-Phase C) 测试.

覆盖:
  * MemoryEntry.space_id 的 save → get_by_id 往返
  * list_permanent 受众粗筛: 自己桶 + PUBLIC (+ 本空间非 SOURCE_RESTRICTED)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
async def store(tmp_path: Path) -> SqliteMemoryStore:
    s = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await s.connect()
    yield s
    await s.close()


def _permanent(
    idx: str,
    source_user: str | None,
    visibility: Visibility = Visibility.SOURCE_RESTRICTED,
    space_id: str | None = None,
) -> MemoryEntry:
    e = MemoryEntry.create(
        content=f"永久记忆 {idx}", role="user", source_user=source_user,
        memory_type=MemoryType.PERMANENT, importance=0.8,
    )
    e.visibility = visibility
    e.space_id = space_id
    return e


@pytest.mark.asyncio
async def test_space_id_roundtrip(store: SqliteMemoryStore) -> None:
    e = _permanent("1", "bob", space_id="g1")
    await store.save(e)
    loaded = await store.get_by_id(e.id)
    assert loaded is not None
    assert loaded.space_id == "g1"

    e2 = _permanent("2", "bob")
    await store.save(e2)
    loaded2 = await store.get_by_id(e2.id)
    assert loaded2 is not None
    assert loaded2.space_id is None


@pytest.mark.asyncio
async def test_list_permanent_audience_superset(store: SqliteMemoryStore) -> None:
    await store.save(_permanent("own", "bob"))
    await store.save(_permanent("other-private", "alice"))
    await store.save(_permanent("public", "alice", visibility=Visibility.PUBLIC))
    await store.save(_permanent(
        "space-shared", "alice", visibility=Visibility.FRIENDS_ONLY, space_id="g1",
    ))
    await store.save(_permanent(
        "space-restricted", "alice", visibility=Visibility.SOURCE_RESTRICTED, space_id="g1",
    ))

    # 私聊粗筛: 自己 + public
    perms = await store.list_permanent("bob", limit=20)
    contents = {p.content for p in perms}
    assert contents == {"永久记忆 own", "永久记忆 public"}

    # 群聊粗筛: 自己 + public + 本空间 (不含 SOURCE_RESTRICTED)
    perms_g1 = await store.list_permanent("bob", limit=20, space_id="g1")
    contents_g1 = {p.content for p in perms_g1}
    assert contents_g1 == {"永久记忆 own", "永久记忆 public", "永久记忆 space-shared"}
    assert "永久记忆 other-private" not in contents_g1
    assert "永久记忆 space-restricted" not in contents_g1

    # 无用户 (非归属): 仅 public
    perms_none = await store.list_permanent(None, limit=20)
    assert {p.content for p in perms_none} == {"永久记忆 public"}
