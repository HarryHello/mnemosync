"""短期记忆装填逻辑 (v0.2.6) 测试.

覆盖:
  * estimate_tokens 单调性 & 空串处理
  * trim_by_budget: 从尾部保留, 预算不足时最老的先丢
  * build_short_term_history: 时间窗 + 模型窗协同
  * context_length 缺失走兜底
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.core.memory.short_term import (
    DEFAULT_CONTEXT_LENGTH_FALLBACK,
    build_short_term_history,
    estimate_tokens,
    trim_by_budget,
)
from src.persistence.conversation_store import ConversationTurn, SqliteConversationStore


def _turn(role: str, content: str, ts: datetime) -> ConversationTurn:
    return ConversationTurn(
        id=None, role=role, content=content, ts=ts,
        token_count=estimate_tokens(content), source_frontend=None,
    )


def test_estimate_tokens_reasonable() -> None:
    assert estimate_tokens("") == 8  # overhead only
    assert estimate_tokens("hello") == 8 + 2  # 5 // 2 = 2
    assert estimate_tokens("你好世界") == 8 + 2  # 4 // 2 = 2
    # 单调性
    assert estimate_tokens("a" * 100) < estimate_tokens("a" * 200)


def test_trim_by_budget_keeps_tail() -> None:
    now = datetime.now(UTC)
    turns = [
        _turn("user", "old-1", now - timedelta(hours=5)),
        _turn("assistant", "old-2", now - timedelta(hours=4)),
        _turn("user", "mid", now - timedelta(hours=3)),
        _turn("assistant", "recent", now - timedelta(hours=1)),
    ]
    # 大预算: 全保留
    kept, used, dropped = trim_by_budget(turns, budget=10_000)
    assert [t.content for t in kept] == ["old-1", "old-2", "mid", "recent"]
    assert dropped == 0
    # 小预算: 只保留最新的
    kept, used, dropped = trim_by_budget(turns, budget=15)  # 每条 estimate 约 10
    assert kept[-1].content == "recent"
    # 至少保留了最新, 最老的被丢
    assert "old-1" not in [t.content for t in kept]


def test_trim_by_budget_zero_returns_empty() -> None:
    now = datetime.now(UTC)
    turns = [_turn("user", "x", now)]
    kept, used, dropped = trim_by_budget(turns, budget=0)
    assert kept == []
    assert used == 0
    assert dropped == 1


@pytest.mark.asyncio
async def test_build_short_term_history_time_and_model_windows(tmp_path: Path) -> None:
    store = SqliteConversationStore(str(tmp_path / "c.db"))
    await store.connect()
    try:
        now = datetime.now(UTC)
        # 3 条窗内, 1 条窗外
        await store.append("user", "太老了", token_count=10, ts=now - timedelta(days=10))
        await store.append("user", "内-1", token_count=10, ts=now - timedelta(days=5))
        await store.append("assistant", "内-2", token_count=10, ts=now - timedelta(days=2))
        await store.append("user", "刚才", token_count=10, ts=now - timedelta(hours=1))

        # 大 ctx: 3 条都留下 (窗外那条直接被时间窗过滤)
        built = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=32_000, system_text="sys", new_user_text="q",
            max_tokens_hint=1024,
        )
        assert built.total_candidates == 3
        assert built.kept == 3
        assert [m["content"] for m in built.conversation_history] == ["内-1", "内-2", "刚才"]
        assert built.active_participants == []

        # 极小 ctx: 保留区 (>=512) 已经吃掉 ctx, 预算被夹到 0
        built2 = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=500,  # 500 - 9 - 8 - 512 < 0 → budget=0
            system_text="sys", new_user_text="q",
            max_tokens_hint=None,
        )
        assert built2.budget == 0
        assert built2.kept == 0
        assert built2.total_candidates == 3  # 时间窗内候选仍统计到
        assert built2.dropped_by_budget == 3

        # 中等 ctx: 只够装最新那条
        built3 = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=2048,
            system_text="sys", new_user_text="q",
            max_tokens_hint=512,  # 保留区 = 512, 剩下装 history 的预算 ~1500
        )
        assert built3.kept >= 1
        # 最新那条一定在
        assert built3.conversation_history[-1]["content"] == "刚才"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_build_short_term_history_space_isolation(tmp_path: Path) -> None:
    """v0.3.0: space_id 非空时只装填本空间流水, 不泄入其他空间对话."""
    store = SqliteConversationStore(str(tmp_path / "c3.db"))
    await store.connect()
    try:
        now = datetime.now(UTC)
        await store.append("user", "群A的话", token_count=10, space_id="group-a",
                           ts=now - timedelta(hours=2))
        await store.append("assistant", "群A回复", token_count=10, space_id="group-a",
                           ts=now - timedelta(hours=1))
        await store.append("user", "群B的话", token_count=10, space_id="group-b",
                           ts=now - timedelta(minutes=30))
        await store.append("user", "私聊的话", token_count=10, ts=now - timedelta(minutes=10))

        # 指定 group-a: 只看到群A的两条
        built = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=32_000, system_text="sys", new_user_text="q",
            max_tokens_hint=1024,
            space_id="group-a",
        )
        assert [m["content"] for m in built.conversation_history] == ["群A的话", "群A回复"]

        # 不指定 space: 退化为全局流水 (单用户私聊场景, 四条全见)
        built_all = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=32_000, system_text="sys", new_user_text="q",
            max_tokens_hint=1024,
        )
        assert built_all.total_candidates == 4
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_group_history_labels_and_lists_distinct_participants(tmp_path: Path) -> None:
    store = SqliteConversationStore(str(tmp_path / "participants.db"))
    await store.connect()
    try:
        now = datetime.now(UTC)
        await store.append(
            "user", "晚上好", token_count=10, space_id="g",
            display_name_snapshot="Harry", external_key_snapshot="1914089741",
            source_frontend="astrbot", ts=now - timedelta(minutes=3),
        )
        await store.append(
            "user", "我来了", token_count=10, space_id="g",
            display_name_snapshot="马达", external_key_snapshot="486394990",
            source_frontend="astrbot", ts=now - timedelta(minutes=2),
        )
        await store.append(
            "user", "又说一句", token_count=10, space_id="g",
            display_name_snapshot="Harry", external_key_snapshot="1914089741",
            source_frontend="astrbot", ts=now - timedelta(minutes=1),
        )

        built = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=32_000, system_text="sys", new_user_text="q",
            max_tokens_hint=1024, space_id="g",
        )

        assert built.conversation_history == [
            {"role": "user", "content": "[Harry | astrbot 1914089741]: 晚上好"},
            {"role": "user", "content": "[马达 | astrbot 486394990]: 我来了"},
            {"role": "user", "content": "[Harry | astrbot 1914089741]: 又说一句"},
        ]
        assert built.active_participants == [
            "马达 | astrbot 486394990",
            "Harry | astrbot 1914089741",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_build_short_term_history_context_length_fallback(tmp_path: Path) -> None:
    """context_length=None 时走兜底 (8192), 不该崩."""
    store = SqliteConversationStore(str(tmp_path / "c2.db"))
    await store.connect()
    try:
        now = datetime.now(UTC)
        await store.append("user", "hi", token_count=10, ts=now - timedelta(hours=1))
        built = await build_short_term_history(
            store=store, now=now, window_days=7,
            context_length=None, system_text="sys", new_user_text="q",
            max_tokens_hint=512,
        )
        # 兜底后预算合理, 应该能装下
        assert built.kept == 1
        # 兜底行为的证据: budget < DEFAULT_CONTEXT_LENGTH_FALLBACK 但 > 0
        assert 0 < built.budget < DEFAULT_CONTEXT_LENGTH_FALLBACK
    finally:
        await store.close()
