"""客户端 OpenAI 工具事务尾部提取与校验.

普通客户端历史仍不可信；本模块只接纳紧跟最后一条 user 消息之后的标准
``assistant(tool_calls) -> tool`` 连续事务片段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_TOOL_TRANSACTION_MESSAGES = 20
MAX_TOOL_TRANSACTION_BYTES = 64 * 1024


class ToolTransactionError(ValueError):
    """客户端工具事务尾部不符合 OpenAI 协议约束."""


@dataclass(frozen=True)
class ToolTransactionTail:
    """经过校验、可以接到服务器历史末端的工具事务."""

    messages: list[dict[str, Any]]
    root_user_content: str


def extract_tool_transaction_tail(
    messages: list[dict[str, Any]],
    available_tools: list[dict[str, Any]] | None,
) -> ToolTransactionTail | None:
    """提取当前请求末尾的已执行工具事务.

    仅当最后一条消息是 ``role=tool`` 时视为工具续轮。普通请求返回 None；
    尾部形状或引用关系非法时抛 ``ToolTransactionError``，避免把旧 user 消息
    误当成新输入重复处理。
    """
    if not messages or messages[-1].get("role") != "tool":
        return None

    if not available_tools:
        raise ToolTransactionError("工具结果回传请求必须同时提供 tools")

    root_index = _last_user_index(messages)
    if root_index is None:
        raise ToolTransactionError("工具事务缺少根 user 消息")

    tail = messages[root_index + 1 :]
    if not tail or len(tail) > MAX_TOOL_TRANSACTION_MESSAGES:
        raise ToolTransactionError(
            f"工具事务消息数必须在 1..{MAX_TOOL_TRANSACTION_MESSAGES} 之间"
        )
    if tail[0].get("role") != "assistant" or not tail[0].get("tool_calls"):
        raise ToolTransactionError("工具事务必须以 assistant(tool_calls) 开始")

    try:
        encoded_size = len(json.dumps(tail, ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise ToolTransactionError("工具事务不是可序列化的 JSON") from exc
    if encoded_size > MAX_TOOL_TRANSACTION_BYTES:
        raise ToolTransactionError("工具事务体积超过 64 KiB")

    tool_names = _available_tool_names(available_tools)
    sanitized: list[dict[str, Any]] = []
    pending: dict[str, str] = {}
    seen_call_ids: set[str] = set()
    completed_groups = 0

    for message in tail:
        role = message.get("role")
        if role == "assistant":
            if pending:
                raise ToolTransactionError("下一轮 assistant 前仍有未返回结果的工具调用")
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                raise ToolTransactionError("工具事务中的 assistant 必须包含 tool_calls")

            clean_calls: list[dict[str, Any]] = []
            for call in calls:
                call_id, name, arguments = _validate_tool_call(
                    call,
                    tool_names=tool_names,
                    seen_call_ids=seen_call_ids,
                )
                seen_call_ids.add(call_id)
                pending[call_id] = name
                clean_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                )

            sanitized.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": clean_calls,
                }
            )
            continue

        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ToolTransactionError("tool_call_id 未引用当前待完成的工具调用")
            expected_name = pending.pop(call_id)
            supplied_name = message.get("name")
            if supplied_name is not None and supplied_name != expected_name:
                raise ToolTransactionError("tool 消息名称与 tool_call_id 对应函数不一致")

            content = message.get("content")
            if not isinstance(content, str):
                raise ToolTransactionError("tool 消息 content 必须是字符串")
            clean_tool: dict[str, Any] = {
                "role": "tool",
                "content": content,
                "tool_call_id": call_id,
            }
            if supplied_name is not None:
                clean_tool["name"] = supplied_name
            sanitized.append(clean_tool)
            if not pending:
                completed_groups += 1
            continue

        raise ToolTransactionError("工具事务尾部只能包含 assistant 和 tool 消息")

    if not sanitized or sanitized[0].get("role") != "assistant":
        raise ToolTransactionError("工具事务必须以 assistant(tool_calls) 开始")
    if pending:
        raise ToolTransactionError("工具事务存在未返回结果的工具调用")
    if completed_groups == 0:
        raise ToolTransactionError("工具事务不包含完整的 tool_calls → tool 轮次")

    root_content = messages[root_index].get("content")
    if not isinstance(root_content, str):
        root_content = ""
    return ToolTransactionTail(messages=sanitized, root_user_content=root_content)


def append_tool_transaction_context(
    history: list[dict[str, Any]],
    transaction: ToolTransactionTail,
) -> list[dict[str, Any]]:
    """把已校验事务接到服务器历史末端，必要时补根 user 消息.

    正常情况下根消息已由首次请求写入服务器事件流；流式客户端可能在
    ``[DONE]`` 后立即回传工具结果，早于首次请求的异步回写，因此需要兜底补入。
    """
    combined = list(history)
    root = transaction.root_user_content.strip()
    if root and not any(
        _matches_root_user(message, root)
        for message in reversed(combined[-12:])
    ):
        combined.append({"role": "user", "content": root})
    combined.extend(transaction.messages)
    return combined


def _matches_root_user(message: dict[str, Any], root: str) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    return (
        stripped == root
        or stripped.endswith(f"]: {root}")
        or stripped.endswith(f"\n{root}\n</current_speaker>")
    )


def _last_user_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _available_tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    if not names:
        raise ToolTransactionError("tools 中没有可用的 function 定义")
    return names


def _validate_tool_call(
    call: Any,
    *,
    tool_names: set[str],
    seen_call_ids: set[str],
) -> tuple[str, str, str]:
    if not isinstance(call, dict) or call.get("type", "function") != "function":
        raise ToolTransactionError("仅支持 function 类型的 tool_calls")

    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id or call_id in seen_call_ids:
        raise ToolTransactionError("tool call id 缺失或重复")

    function = call.get("function")
    if not isinstance(function, dict):
        raise ToolTransactionError("tool call 缺少 function")
    name = function.get("name")
    if not isinstance(name, str) or name not in tool_names:
        raise ToolTransactionError("tool call 使用了本轮未提供的函数")

    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ToolTransactionError("function.arguments 必须是 JSON 字符串")
    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ToolTransactionError("function.arguments 不是合法 JSON") from exc
    if not isinstance(parsed_arguments, dict):
        raise ToolTransactionError("function.arguments 必须编码 JSON 对象")

    return call_id, name, arguments
