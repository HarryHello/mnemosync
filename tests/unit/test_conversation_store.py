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


# ─── v0.3.0 空间事件流 ────────────────────────────────


@pytest.mark.asyncio
async def test_committed_sequence_assigned_per_space(store: SqliteConversationStore) -> None:
    """同空间内序号从 0 单调递增; 空间之间互不干扰."""
    now = datetime.now(timezone.utc)
    await store.append("user", "群A-1", token_count=1, space_id="group-a", ts=now)
    await store.append("assistant", "群A-2", token_count=1, space_id="group-a",
                       ts=now + timedelta(seconds=1))
    await store.append("user", "群B-1", token_count=1, space_id="group-b",
                       ts=now + timedelta(seconds=2))
    await store.append("user", "群A-3", token_count=1, space_id="group-a",
                       ts=now + timedelta(seconds=3))

    a = await store.list_for_space("group-a")
    assert [t.content for t in a] == ["群A-1", "群A-2", "群A-3"]
    assert [t.committed_sequence for t in a] == [0, 1, 2]

    b = await store.list_for_space("group-b")
    assert [t.content for t in b] == ["群B-1"]
    assert [t.committed_sequence for t in b] == [0]  # B 的序号独立从 0 开始


@pytest.mark.asyncio
async def test_no_sequence_without_space(store: SqliteConversationStore) -> None:
    """私聊/非归属 (space_id=None) 的轮次不分配序号, 仍按 ts 定序."""
    now = datetime.now(timezone.utc)
    await store.append("user", "私聊", token_count=1, ts=now)
    turns = await store.list_since(now - timedelta(days=1))
    assert len(turns) == 1
    assert turns[0].committed_sequence is None
    assert turns[0].space_id is None


@pytest.mark.asyncio
async def test_list_for_space_isolates_spaces(store: SqliteConversationStore) -> None:
    """群聊装填只读本空间 — 其他群/私聊的对话不能泄入."""
    now = datetime.now(timezone.utc)
    await store.append("user", "群A的话", token_count=1, space_id="group-a", ts=now)
    await store.append("user", "群B的话", token_count=1, space_id="group-b",
                       ts=now + timedelta(seconds=1))
    await store.append("user", "私聊的话", token_count=1, ts=now + timedelta(seconds=2))

    a = await store.list_for_space("group-a")
    assert [t.content for t in a] == ["群A的话"]
    # 未知空间返回空, 不报错
    assert await store.list_for_space("group-unknown") == []


@pytest.mark.asyncio
async def test_list_for_space_time_window(store: SqliteConversationStore) -> None:
    """list_for_space 的 since 过滤与全局 list_since 口径一致."""
    now = datetime.now(timezone.utc)
    await store.append("user", "太老了", token_count=1, space_id="g",
                       ts=now - timedelta(days=10))
    await store.append("user", "窗内", token_count=1, space_id="g",
                       ts=now - timedelta(hours=1))
    turns = await store.list_for_space("g", since=now - timedelta(days=7))
    assert [t.content for t in turns] == ["窗内"]


@pytest.mark.asyncio
async def test_late_arrival_flag(store: SqliteConversationStore) -> None:
    """事件时间早于空间内最新已提交时间 → late_arrival=True (乱序到达)."""
    now = datetime.now(timezone.utc)
    await store.append("user", "正常-1", token_count=1, space_id="g", ts=now)
    await store.append("user", "正常-2", token_count=1, space_id="g",
                       ts=now + timedelta(seconds=10))
    # 平台重发/乱序: 事件时间早于已提交的 "正常-2"
    await store.append("user", "迟到的", token_count=1, space_id="g",
                       ts=now + timedelta(seconds=5))

    turns = await store.list_for_space("g")
    flags = {t.content: t.late_arrival for t in turns}
    assert flags["正常-1"] is False
    assert flags["正常-2"] is False
    assert flags["迟到的"] is True
    # 乱序到达不影响序号单调: 仍排在最后提交
    assert [t.committed_sequence for t in turns] == [0, 1, 2]


@pytest.mark.asyncio
async def test_external_event_id_roundtrip(store: SqliteConversationStore) -> None:
    now = datetime.now(timezone.utc)
    await store.append("user", "带事件ID", token_count=1, space_id="g",
                       external_event_id="qq-msg-12345", ts=now)
    turns = await store.list_for_space("g")
    assert turns[0].external_event_id == "qq-msg-12345"


@pytest.mark.asyncio
async def test_migration_from_v02x_schema(tmp_path: Path) -> None:
    """v0.2.x 老库 (无新列) 升级后能正常打开.

    回归: 索引 idx_conv_space_seq 引用新列, 必须在 ALTER 迁移之后创建,
    否则老库启动即崩 (no such column: committed_sequence)。
    """
    import sqlite3

    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TIMESTAMP NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            source_frontend TEXT
        )
    """)
    con.execute(
        "INSERT INTO conversation_turns (role, content, ts, token_count) "
        "VALUES ('user', '老数据', '2026-01-01T00:00:00+00:00', 4)"
    )
    con.commit()
    con.close()

    s = SqliteConversationStore(str(db_path))
    await s.connect()  # 老库 → 触发迁移, 不应抛 OperationalError
    try:
        turns = await s.list_recent()
        assert len(turns) == 1
        assert turns[0].content == "老数据"
        assert turns[0].actor_id is None
        assert turns[0].committed_sequence is None
        assert turns[0].late_arrival is False
        # 迁移后新写入正常
        await s.append("user", "新数据", token_count=1, space_id="g")
        assert (await s.list_for_space("g"))[0].committed_sequence == 0
    finally:
        await s.close()
