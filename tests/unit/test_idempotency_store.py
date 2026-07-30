"""幂等键存储 (v0.3.0 Sub-Phase B) 测试.

覆盖:
  * record + get 往返
  * 重复 record 保留首次结果 (INSERT OR IGNORE)
  * 不同 integration_id 之间隔离 (同一事件 ID 不冲突)
  * prune_before 时间窗清理
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.persistence.idempotency_store import SqliteIdempotencyStore


@pytest.fixture
async def store(tmp_path: Path) -> SqliteIdempotencyStore:
    s = SqliteIdempotencyStore(str(tmp_path / "idemp.db"))
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_record_and_get_roundtrip(store: SqliteIdempotencyStore) -> None:
    await store.record("key-1", "evt-100", "chatcmpl-abc", "你好！")
    rec = await store.get("key-1", "evt-100")
    assert rec is not None
    assert rec.integration_id == "key-1"
    assert rec.external_event_id == "evt-100"
    assert rec.event_id == "chatcmpl-abc"
    assert rec.response_text == "你好！"
    assert rec.created_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_get_miss_returns_none(store: SqliteIdempotencyStore) -> None:
    assert await store.get("key-1", "never-seen") is None


@pytest.mark.asyncio
async def test_duplicate_record_keeps_first(store: SqliteIdempotencyStore) -> None:
    """平台重发 + 竞态并发写入时, 首次结果不被覆盖."""
    await store.record("key-1", "evt-1", "chatcmpl-first", "第一次回复")
    await store.record("key-1", "evt-1", "chatcmpl-second", "第二次回复")
    rec = await store.get("key-1", "evt-1")
    assert rec is not None
    assert rec.event_id == "chatcmpl-first"
    assert rec.response_text == "第一次回复"
    assert await store.count() == 1


@pytest.mark.asyncio
async def test_same_event_id_across_integrations(store: SqliteIdempotencyStore) -> None:
    """不同 API Key (集成) 的事件 ID 各自独立 — 主键含 integration_id."""
    await store.record("key-a", "evt-42", "chatcmpl-a", "来自 A")
    await store.record("key-b", "evt-42", "chatcmpl-b", "来自 B")
    rec_a = await store.get("key-a", "evt-42")
    rec_b = await store.get("key-b", "evt-42")
    assert rec_a is not None and rec_a.response_text == "来自 A"
    assert rec_b is not None and rec_b.response_text == "来自 B"
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_prune_before(store: SqliteIdempotencyStore) -> None:
    await store.record("key-1", "evt-old", "chatcmpl-old", "旧")
    now = datetime.now(UTC)
    # 记录刚写入, cutoff 取未来 → 全部清掉
    n = await store.prune_before(now + timedelta(seconds=5))
    assert n == 1
    assert await store.count() == 0
    assert await store.get("key-1", "evt-old") is None


@pytest.mark.asyncio
async def test_prune_before_keeps_recent(store: SqliteIdempotencyStore) -> None:
    await store.record("key-1", "evt-new", "chatcmpl-new", "新")
    # cutoff 取过去 → 刚写入的记录保留
    n = await store.prune_before(datetime.now(UTC) - timedelta(days=1))
    assert n == 0
    assert await store.count() == 1
