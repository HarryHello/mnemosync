"""跨前端对话流水存储 (v0.2.6) 测试.

覆盖:
  * append + list_since + list_recent 的基本 CRUD
  * 时间窗过滤 (list_since)
  * delete_before / delete_all 的删除语义
  * source_frontend 作为元数据保留
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.persistence.conversation_store import SqliteConversationStore


@pytest.fixture
async def store(tmp_path: Path) -> SqliteConversationStore:
    s = SqliteConversationStore(str(tmp_path / "conv.db"))
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_append_and_list_recent(store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    await store.append("user", "你好", token_count=4, source_frontend="astrbot", ts=now)
    await store.append("assistant", "hi", token_count=2, source_frontend="astrbot",
                       ts=now + timedelta(seconds=1))
    recent = await store.list_recent(limit=10)
    assert len(recent) == 2
    # list_recent 按 ts 降序
    assert recent[0].role == "assistant"
    assert recent[1].role == "user"
    assert recent[0].source_frontend == "astrbot"


@pytest.mark.asyncio
async def test_list_since_filters_by_time(store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    await store.append("user", "old", token_count=1, ts=now - timedelta(days=10))
    await store.append("assistant", "recent", token_count=1, ts=now - timedelta(hours=1))
    turns = await store.list_since(now - timedelta(days=7))
    # 只保留 7 天内的
    assert len(turns) == 1
    assert turns[0].content == "recent"
    # list_since 按 ts 升序
    await store.append("user", "newest", token_count=1, ts=now)
    turns2 = await store.list_since(now - timedelta(days=7))
    assert len(turns2) == 2
    assert turns2[0].content == "recent"
    assert turns2[1].content == "newest"


@pytest.mark.asyncio
async def test_delete_before(store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    await store.append("user", "old", token_count=1, ts=now - timedelta(days=10))
    await store.append("assistant", "recent", token_count=1, ts=now - timedelta(hours=1))
    n = await store.delete_before(now - timedelta(days=7))
    assert n == 1
    assert await store.count() == 1
    remaining = await store.list_recent()
    assert remaining[0].content == "recent"


@pytest.mark.asyncio
async def test_delete_all(store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    await store.append("user", "a", token_count=1, ts=now)
    await store.append("assistant", "b", token_count=1, ts=now)
    n = await store.delete_all()
    assert n == 2
    assert await store.count() == 0


@pytest.mark.asyncio
async def test_invalid_role_rejected(store: SqliteConversationStore) -> None:
    with pytest.raises(ValueError):
        await store.append("system", "x", token_count=1)


@pytest.mark.asyncio
async def test_multi_frontend_appended_to_same_bucket(store: SqliteConversationStore) -> None:
    """v0.2.6 关键不变量: 多个前端 = 同一个用户, 全部落进同一 bucket.

    没有按 source_frontend 分区, 装填时统一读所有记录 — 这就是 Mnemosync
    "跨前端统一记忆" 的技术承诺。
    """
    now = datetime.now(timezone.utc)
    await store.append("user", "在 astrbot 说的", token_count=8, source_frontend="astrbot", ts=now)
    await store.append("user", "在 airi 说的", token_count=7, source_frontend="airi",
                       ts=now + timedelta(seconds=1))
    await store.append("user", "在 web 面板说的", token_count=7, source_frontend="panel-debug",
                       ts=now + timedelta(seconds=2))
    turns = await store.list_since(now - timedelta(days=1))
    assert len(turns) == 3
    # 保序 & source_frontend 只是元数据
    assert [t.source_frontend for t in turns] == ["astrbot", "airi", "panel-debug"]
