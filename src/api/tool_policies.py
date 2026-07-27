"""API Key 级客户端工具策略.

在 MAIN 模型看到工具定义前、以及 tool_calls 返回客户端前执行两层过滤:
1. 入站过滤: 从请求 tools 中移除被策略禁止的函数, 避免模型知道其存在
2. 出站过滤: 从响应 tool_calls 中移除被策略禁止的函数, 作为模型未遵守时的最后防线

策略配置通过 API Key 的 note 字段或 identity strategy config 携带。
不需要客户端修改, 纯服务器侧策略。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolPolicy:
    """单个 API Key 的工具调用策略."""

    allowed_tools: frozenset[str] | None = None   # None = 全部允许
    denied_tools: frozenset[str] = frozenset()
    max_calls_per_round: int = 10
    require_confirmation: frozenset[str] = frozenset()
    cooldown_seconds: dict[str, int] = field(default_factory=dict)
    _last_call_at: dict[str, float] = field(default_factory=dict, compare=False)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """工具是否允许出现在本轮请求中."""
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False
        return True

    def is_call_permitted(self, tool_name: str, now: float | None = None) -> bool:
        """当前时刻是否允许执行该工具调用 (含冷却检查)."""
        if not self.is_tool_allowed(tool_name):
            return False
        cooldown = self.cooldown_seconds.get(tool_name, 0)
        if cooldown <= 0:
            return True
        t = now or time.monotonic()
        last = self._last_call_at.get(tool_name, 0)
        return (t - last) >= cooldown

    def record_call(self, tool_name: str, now: float | None = None) -> None:
        """记录一次工具调用, 用于冷却计时."""
        self._last_call_at[tool_name] = now or time.monotonic()


def filter_client_tools(
    tools: list[dict[str, Any]] | None,
    policy: ToolPolicy | None,
) -> list[dict[str, Any]] | None:
    """入站过滤: 从客户端工具定义中移除策略禁止的工具."""
    if not tools or not policy:
        return tools
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        func = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = func.get("name", "") if isinstance(func, dict) else ""
        if name and policy.is_tool_allowed(name):
            filtered.append(tool)
    return filtered if filtered else None


def filter_tool_calls(
    tool_calls: list[dict[str, Any]] | None,
    policy: ToolPolicy | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """出站过滤: 从响应 tool_calls 中移除被策略禁止的调用.

    Returns:
        (filtered_calls, removed_names) - 通过过滤的工具调用和被移除的工具名
    """
    if not tool_calls:
        return [], []
    if not policy:
        return list(tool_calls), []
    now = time.monotonic()
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for call in tool_calls[: policy.max_calls_per_round]:
        func = call.get("function", {}) if isinstance(call, dict) else {}
        name = func.get("name", "") if isinstance(func, dict) else ""
        if name and policy.is_call_permitted(name, now=now):
            kept.append(call)
            policy.record_call(name, now=now)
        else:
            removed.append(name)
    return kept, removed


def load_tool_policy(config_str: str | None) -> ToolPolicy | None:
    """从 JSON 配置字符串解析工具策略.

    配置格式 (存储在 identity strategy config 的 tool_policy 字段):
    {
        "allowed_tools": ["poke", "react"],   // 可选, 白名单
        "denied_tools": ["send_message"],     // 可选, 黑名单
        "max_calls_per_round": 3,
        "cooldown_seconds": {"poke": 30}
    }

    API Key note 中以 TOOL_POLICY:{json} 前缀携带的策略优先级低于 strategy
    config 中的 tool_policy 字段。
    """
    if not config_str:
        return None
    try:
        data = json.loads(config_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    allowed = data.get("allowed_tools")
    denied = data.get("denied_tools", [])
    max_round = int(data.get("max_calls_per_round", 10))
    cooldown = data.get("cooldown_seconds", {})
    require_confirm = data.get("require_confirmation", [])

    return ToolPolicy(
        allowed_tools=frozenset(allowed) if isinstance(allowed, list) else None,
        denied_tools=frozenset(denied) if isinstance(denied, list) else frozenset(),
        max_calls_per_round=max(1, max_round),
        require_confirmation=frozenset(require_confirm) if isinstance(require_confirm, list) else frozenset(),
        cooldown_seconds={k: int(v) for k, v in cooldown.items() if isinstance(v, (int, float)) and v > 0} if isinstance(cooldown, dict) else {},
    )
