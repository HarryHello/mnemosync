"""update_addressing tool 单元测试 (v0.2.10)."""

from __future__ import annotations

import pytest

from src.persistence.memory_store import SqliteMemoryStore
from src.tools import make_update_addressing_tool


@pytest.fixture
async def store(tmp_path):
    s = SqliteMemoryStore(str(tmp_path / "memory.db"))
    await s.init_db()
    return s


async def test_tool_writes_relationship_and_audit(store):
    tool = make_update_addressing_tool(store, "default", "alice")
    result = await tool.ainvoke({
        "user_addressing": "小哥",
        "reason": "用户在当前消息中显式请求改称呼",
    })
    assert result["updated_fields"] == ["user_addressing"]
    assert result["prev"]["user_addressing"] is None
    assert result["current"]["user_addressing"] == "小哥"
    assert len(result["audit_ids"]) == 1

    rel = await store.get_relationship("default", "alice")
    assert rel.user_addressing == "小哥"
    audit = await store.list_relationship_audit("default", "alice")
    assert audit[0].source == "agent"


async def test_tool_rejects_short_reason(store):
    tool = make_update_addressing_tool(store, "default", "alice")
    with pytest.raises(ValueError, match="reason 至少"):
        await tool.ainvoke({
            "user_addressing": "小哥",
            "reason": "太短",
        })


async def test_tool_rejects_empty_reason(store):
    tool = make_update_addressing_tool(store, "default", "alice")
    with pytest.raises(ValueError, match="reason 至少"):
        await tool.ainvoke({
            "user_addressing": "小哥",
            "reason": "",
        })


async def test_tool_rejects_all_none_fields(store):
    tool = make_update_addressing_tool(store, "default", "alice")
    with pytest.raises(ValueError, match="至少需要传入一个字段"):
        await tool.ainvoke({
            "reason": "有正常长度的说明文字, 但没传字段",
        })


async def test_tool_binds_persona_and_user_ids(store):
    """Agent 传参无法覆盖闭包绑定的 persona_id / user_id."""
    tool = make_update_addressing_tool(store, "default", "alice")
    # Agent 输入不含 persona_id / user_id, tool schema 也不暴露, 写入落到绑定值
    await tool.ainvoke({
        "user_addressing": "aliceの称呼",
        "reason": "验证闭包绑定的 persona_id/user_id 生效",
    })
    # bob 用户下无变化
    rel_bob = await store.get_relationship("default", "bob")
    assert rel_bob is None
    rel_alice = await store.get_relationship("default", "alice")
    assert rel_alice.user_addressing == "aliceの称呼"


async def test_tool_updates_all_three_fields(store):
    tool = make_update_addressing_tool(store, "default", "alice")
    result = await tool.ainvoke({
        "persona_addressing": "人家",
        "user_addressing": "亲爱的",
        "context": "恋人",
        "reason": "用户明确表白且被接受, 关系升级",
    })
    assert set(result["updated_fields"]) == {"persona_addressing", "user_addressing", "context"}
    rel = await store.get_relationship("default", "alice")
    assert rel.persona_addressing == "人家"
    assert rel.user_addressing == "亲爱的"
    assert rel.context == "恋人"


async def test_tool_returns_bound_actor_id(store):
    """v0.3.0: actor_id 闭包绑定, 返回给调用方供溯源, 不影响写入目标."""
    tool = make_update_addressing_tool(
        store, "default", "group_zhangsan", actor_id="actor_qq_12345",
    )
    result = await tool.ainvoke({
        "user_addressing": "三哥",
        "reason": "群聊中该 Actor 自我介绍后确立称呼",
    })
    assert result["actor_id"] == "actor_qq_12345"
    # 写入目标仍是绑定的 effective_user_id (组), 不是 actor
    rel = await store.get_relationship("default", "group_zhangsan")
    assert rel.user_addressing == "三哥"
    assert await store.get_relationship("default", "actor_qq_12345") is None
