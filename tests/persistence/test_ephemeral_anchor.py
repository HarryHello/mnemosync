"""EPHEMERAL 情绪锚点记忆测试 (v0.4.1, RFC §6)."""

from __future__ import annotations

import pytest
from src.core.memory.models import MemoryEntry, MemoryType


@pytest.mark.asyncio
async def test_save_ephemeral_anchor_and_load_by_subject(memory_store) -> None:
    """写入带 subject 的 EPHEMERAL 记忆 → 按 subject 确定性加载."""
    entry = MemoryEntry.create(
        content="被当众羞辱，感到恼火",
        role="system",
        source_user="user_a",
        memory_type=MemoryType.EPHEMERAL,
        importance=0.8,
        decay_rate=0.95,
    )
    entry.subject_actor_id = "actor_qq_123"
    await memory_store.save(entry)

    hits = await memory_store.list_ephemeral_by_subject("actor_qq_123")
    assert len(hits) == 1
    assert hits[0].content == "被当众羞辱，感到恼火"
    assert hits[0].memory_type == MemoryType.EPHEMERAL
    assert hits[0].decay_rate == pytest.approx(0.95)

    # 其他 subject / 无 subject 查询 → 不返回
    assert await memory_store.list_ephemeral_by_subject("actor_dc_456") == []


@pytest.mark.asyncio
async def test_ephemeral_anchor_superseded(memory_store) -> None:
    """同 subject 新锚点 supersedes 旧锚点 — 只返回最新."""
    e1 = MemoryEntry.create(
        content="被羞辱，恼火",
        role="system", source_user="u", memory_type=MemoryType.EPHEMERAL,
        importance=0.8, decay_rate=0.95,
    )
    e1.subject_actor_id = "actor_a"
    await memory_store.save(e1)

    e2 = MemoryEntry.create(
        content="对方道歉了，气消了大半",
        role="system", source_user="u", memory_type=MemoryType.EPHEMERAL,
        importance=0.8, decay_rate=0.95,
    )
    e2.subject_actor_id = "actor_a"
    await memory_store.save(e2)
    await memory_store.mark_superseded(e1.id, e2.id)

    hits = await memory_store.list_ephemeral_by_subject("actor_a")
    assert len(hits) == 1
    assert hits[0].id == e2.id


@pytest.mark.asyncio
async def test_ephemeral_anchor_not_in_permanent_listing(memory_store) -> None:
    """EPHEMERAL 不混入永久记忆加载 (类型隔离)."""
    e = MemoryEntry.create(
        content="对 A 恼火",
        role="system", source_user="u", memory_type=MemoryType.EPHEMERAL,
        importance=0.8, decay_rate=0.95,
    )
    e.subject_actor_id = "actor_a"
    from src.core.memory.models import Visibility

    e.visibility = Visibility.PUBLIC
    await memory_store.save(e)

    perms = await memory_store.list_permanent("u", limit=10)
    assert all(p.memory_type != MemoryType.EPHEMERAL for p in perms)
