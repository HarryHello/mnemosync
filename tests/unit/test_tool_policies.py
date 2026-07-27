"""API Key 级客户端工具策略测试."""

from __future__ import annotations

import pytest

from src.api.tool_policies import (
    ToolPolicy,
    filter_client_tools,
    filter_tool_calls,
    load_tool_policy,
)

TOOLS = [
    {"type": "function", "function": {"name": "poke", "parameters": {}}},
    {"type": "function", "function": {"name": "react", "parameters": {}}},
    {"type": "function", "function": {"name": "kick", "parameters": {}}},
]


def test_load_tool_policy_from_json():
    policy = load_tool_policy(
        '{"allowed_tools": ["poke"], "max_calls_per_round": 1}'
    )
    assert policy is not None
    assert policy.is_tool_allowed("poke")
    assert not policy.is_tool_allowed("react")
    assert policy.max_calls_per_round == 1


def test_load_tool_policy_invalid_returns_none():
    assert load_tool_policy(None) is None
    assert load_tool_policy("not json") is None
    assert load_tool_policy("[]") is None


def test_filter_client_tools_whitelist():
    policy = ToolPolicy(allowed_tools=frozenset({"poke"}))
    filtered = filter_client_tools(TOOLS, policy)
    assert len(filtered) == 1
    assert filtered[0]["function"]["name"] == "poke"


def test_filter_client_tools_blacklist():
    policy = ToolPolicy(denied_tools=frozenset({"kick"}))
    filtered = filter_client_tools(TOOLS, policy)
    assert len(filtered) == 2
    names = {t["function"]["name"] for t in filtered}
    assert names == {"poke", "react"}


def test_filter_client_tools_none_policy_passthrough():
    assert filter_client_tools(TOOLS, None) is TOOLS


def test_filter_tool_calls_enforces_max_per_round():
    policy = ToolPolicy(max_calls_per_round=2)
    calls = [
        {"id": f"call_{i}", "function": {"name": "poke", "arguments": "{}"}}
        for i in range(5)
    ]
    kept, removed = filter_tool_calls(calls, policy)
    assert len(kept) == 2
    assert len(removed) == 0  # truncation doesn't add to removed


def test_filter_tool_calls_removes_denied_tools():
    policy = ToolPolicy(denied_tools=frozenset({"kick"}))
    calls = [
        {"id": "call_1", "function": {"name": "poke", "arguments": "{}"}},
        {"id": "call_2", "function": {"name": "kick", "arguments": "{}"}},
        {"id": "call_3", "function": {"name": "react", "arguments": "{}"}},
    ]
    kept, removed = filter_tool_calls(calls, policy)
    assert len(kept) == 2
    assert "kick" in removed


def test_filter_tool_calls_whitelist():
    policy = ToolPolicy(allowed_tools=frozenset({"poke"}))
    calls = [
        {"id": "call_1", "function": {"name": "poke", "arguments": "{}"}},
        {"id": "call_2", "function": {"name": "react", "arguments": "{}"}},
    ]
    kept, removed = filter_tool_calls(calls, policy)
    assert len(kept) == 1
    assert kept[0]["function"]["name"] == "poke"
    assert "react" in removed


def test_cooldown_enforced():
    policy = ToolPolicy(cooldown_seconds={"poke": 60})
    calls = [{"id": "call_1", "function": {"name": "poke", "arguments": "{}"}}]
    now = 1000.0
    kept1, _ = filter_tool_calls(calls, policy)  # type: ignore[arg-type]
    assert len(kept1) == 1
    # 记录已调用; 第二次在冷却内被拒
    policy.record_call("poke", now=now)
    policy._last_call_at.clear()
    policy._last_call_at["poke"] = now
    import time
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(time, "monotonic", lambda: now + 10)
        kept2, removed2 = filter_tool_calls(calls, policy)
    assert len(kept2) == 0
    assert "poke" in removed2


def test_none_policy_passes_through():
    calls = [{"id": "call_1", "function": {"name": "poke", "arguments": "{}"}}]
    kept, removed = filter_tool_calls(calls, None)
    assert len(kept) == 1
    assert removed == []
