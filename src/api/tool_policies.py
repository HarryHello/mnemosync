"""API Key 级客户端工具策略.

在 MAIN 模型看到工具定义前、以及 tool_calls 返回客户端前执行两层过滤:
1. 入站过滤: 从请求 tools 中移除被策略禁止的函数, 避免模型知道其存在
2. 出站过滤: 从响应 tool_calls 中移除被策略禁止的函数, 作为模型未遵守时的最后防线

同时提供确定性工具参数隐私检查, 防止私有记忆通过工具参数泄露。

策略配置通过 API Key 的 note 字段或 identity strategy config 携带。
不需要客户端修改, 纯服务器侧策略。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# UUID 格式 (含连字符或不带连字符)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\b"
)
# 参数体积上限
MAX_TOOL_ARG_BYTES = 2000
# 冷却持久化窗口: 查询最近 N 秒内的工具调用
COOLDOWN_LOOKBACK_SECONDS = 3600  # 1 小时


async def check_persisted_cooldowns(
    conversation_store: Any,
    valid_calls: list[dict[str, Any]],
    policy: ToolPolicy | None,
    *,
    source_user: str | None,
    space_id: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """从 conversation_turns 查询工具调用历史, 持久化冷却检查.

    内存中的冷却 (policy._last_call_at) 只在单请求内生效; 此函数
    从 DB 查询最近的 tool_call 事件, 确保跨请求/重启后冷却仍生效.

    Args:
        conversation_store: SqliteConversationStore 实例
        valid_calls: 已通过其他检查的 tool_calls
        policy: 工具策略 (含 cooldown_seconds)
        source_user: 当前发言者 effective_user_id
        space_id: 当前空间 ID

    Returns:
        (kept_calls, cooldown_violations)
    """
    if not valid_calls or not policy or not policy.cooldown_seconds:
        return list(valid_calls), []

    now = datetime.now(UTC)
    kept: list[dict[str, Any]] = []
    violations: list[str] = []

    for call in valid_calls:
        func = call.get("function", {}) if isinstance(call, dict) else {}
        name = func.get("name", "") if isinstance(func, dict) else ""
        cooldown = policy.cooldown_seconds.get(name, 0)
        if cooldown <= 0:
            kept.append(call)
            continue
        # 查询最近 N 秒内同工具、同用户、同空间的 tool_call 事件数
        # 复用 conversation_store.list_page 的事件类型过滤
        recent, _ = await conversation_store.list_page(
            limit=1,
            offset=0,
            role="assistant",
            effective_user_id=source_user,
            space_id=space_id,
            event_type="tool_call",
            sort_by="ts",
            sort_order="desc",
        )
        # 检查最近的 tool_call 是否在冷却窗口内
        in_cooldown = False
        for turn in recent:
            # 简单检查: 最近的 tool_call 在冷却时间内
            if (now - turn.ts).total_seconds() < cooldown:
                # 进一步检查是否是同一工具 (通过 content 中的 function name)
                if name in (turn.content or ""):
                    in_cooldown = True
                    break
        if in_cooldown:
            violations.append(f"{name}: 冷却中 ({cooldown}s)")
        else:
            kept.append(call)
    return kept, violations


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


def validate_tool_arguments(
    tool_calls: list[dict[str, Any]] | None,
    available_tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """确定性工具参数隐私检查.

    在响应返回客户端前验证工具参数的安全性:
    - 工具名称必须在本轮 tools 定义中
    - arguments 必须是合法 JSON 对象
    - 参数长度不超过上限
    - 参数不得包含内部 UUID (可能泄露内部身份)

    Returns:
        (valid_calls, issues) - 通过检查的调用和被移除的调用及原因
    """
    if not tool_calls:
        return [], []
    valid: list[dict[str, Any]] = []
    issues: list[str] = []
    available_names = {
        t.get("function", {}).get("name")
        for t in (available_tools or [])
        if isinstance(t, dict)
    }
    for call in tool_calls:
        func = call.get("function", {}) if isinstance(call, dict) else {}
        name = func.get("name", "") if isinstance(func, dict) else ""
        if available_names and name not in available_names:
            issues.append(f"{name}: 工具不在本轮定义中")
            continue
        args_str = func.get("arguments", "") if isinstance(func, dict) else ""
        if not args_str:
            issues.append(f"{name}: arguments 为空")
            continue
        try:
            args = json.loads(args_str)
            if not isinstance(args, dict):
                issues.append(f"{name}: arguments 不是 JSON 对象")
                continue
        except json.JSONDecodeError:
            issues.append(f"{name}: arguments 不是合法 JSON")
            continue
        # 参数体积检查
        if len(args_str.encode()) > MAX_TOOL_ARG_BYTES:
            issues.append(f"{name}: 参数体积超过 {MAX_TOOL_ARG_BYTES} bytes")
            continue
        # UUID 泄露检查: 内部 actor/group UUID 不应出现在对外参数中
        uuid_matches = _UUID_RE.findall(args_str)
        if uuid_matches:
            issues.append(f"{name}: 参数包含疑似内部 UUID ({len(uuid_matches)} 个)")
            continue
        valid.append(call)
    return valid, issues


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
