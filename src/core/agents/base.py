"""Agent 基础设施: ReAct 循环驱动.

本地组装请求 → 远端模型推理 → 解析 function_call → 执行工具 → 喂回模型 → 循环.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.infra.forwarder import UpstreamError
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

logger = logging.getLogger(__name__)

# 工具调用函数类型: 接收 name + args, 返回结果（字符串或 dict）
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class ReActStep:
    """ReAct 单步记录（调试/演示用）."""

    round: int
    think: str | None = None        # 模型的文本推理（若有）
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: Any = None
    is_final: bool = False

    def format(self) -> str:
        parts = [f"[Round {self.round}]"]
        if self.think:
            parts.append(f"  Think: {self.think[:100]}")
        if self.tool_name:
            parts.append(f"  Act: {self.tool_name}({self.tool_args})")
            result_str = str(self.tool_result)
            parts.append(f"  Observe: {result_str[:200]}")
        if self.is_final:
            parts.append("  → Final output")
        return "\n".join(parts)


@dataclass
class ReActResult:
    """ReAct 循环结果."""

    output: str                      # 模型最终输出的文本
    steps: list[ReActStep] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def _build_tools_schema(tools: list) -> list[dict]:
    """把 LangChain Tool 列表转为 OpenAI function schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": (
                    t.args_schema.model_json_schema()
                    if hasattr(t, "args_schema") and t.args_schema
                    else {"type": "object", "properties": {}}
                ),
            },
        }
        for t in tools
    ]


async def _execute_tool(tool, args: dict[str, Any]) -> Any:
    """执行单个 LangChain Tool."""
    try:
        return await tool.ainvoke(args)
    except Exception as e:
        logger.error("工具执行失败 %s: %s", tool.name, e)
        return {"error": f"工具执行失败: {e}"}


async def run_react_loop(
    forwarder: MultiForwarder,
    role: ModelType,
    system_prompt: str,
    user_prompt: str,
    tools: list,
    max_iterations: int = 5,
    temperature: float = 0.3,
    extra_body: dict | None = None,
) -> ReActResult:
    """驱动 ReAct 循环.

    流程:
        1. 组装 messages (system + user) + tools schema
        2. 通过 MultiForwarder 按角色候选调用模型
        3. 若模型返回 tool_calls → 执行工具 → 把结果以 role=tool 喂回 → 回到 1
        4. 若模型返回最终内容 → 返回 ReActResult

    Args:
        forwarder: 多候选模型调用通道
        role: 角色 (决定候选列表; 通常 ASSIST)
        system_prompt: system 消息
        user_prompt: user 消息（任务描述）
        tools: LangChain Tool 列表
        max_iterations: 最大循环轮数（防止无限循环）
        temperature: 温度
        extra_body: 额外请求体（如 enable_thinking=False）

    Returns:
        ReActResult, 含最终输出文本和每步记录
    """
    tools_schema = _build_tools_schema(tools)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    steps: list[ReActStep] = []
    extra_body = extra_body or {"enable_thinking": False}  # 默认关闭 thinking 保证结构化

    for round_num in range(1, max_iterations + 1):
        try:
            resp = await forwarder.chat(
                role,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto",
                temperature=temperature,
                extra_body=extra_body,
            )
        except (UpstreamError, Exception) as e:
            return ReActResult(output="", steps=steps, error=f"模型调用失败 round {round_num}: {e}")

        msg = resp["choices"][0]["message"]
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        # 若模型未调工具, 视为最终输出
        if not tool_calls:
            step = ReActStep(round=round_num, think=content, is_final=True)
            steps.append(step)
            logger.info(step.format())
            return ReActResult(output=content, steps=steps)

        # 处理工具调用（可能多个）
        # 先把 assistant 消息（含 tool_calls）加入 messages
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                }
                for tc in tool_calls
            ],
        })

        # 执行每个工具调用
        tool_map = {t.name: t for t in tools}
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                fn_args = {}

            step = ReActStep(
                round=round_num,
                think=content[:200] if content else None,
                tool_name=fn_name,
                tool_args=fn_args,
            )

            tool = tool_map.get(fn_name)
            if tool is None:
                result = {"error": f"未知工具: {fn_name}"}
            else:
                result = await _execute_tool(tool, fn_args)

            step.tool_result = result
            steps.append(step)
            logger.info(step.format())

            # 把工具结果喂回模型
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": (
                    json.dumps(result, ensure_ascii=False)
                    if isinstance(result, (dict, list))
                    else str(result)
                ),
            })

    # 达到最大轮数仍未结束
    return ReActResult(
        output="",
        steps=steps,
        error=f"达到最大轮数 ({max_iterations}) 未得到最终输出",
    )


async def run_simple_completion(
    forwarder: MultiForwarder,
    role: ModelType,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    extra_body: dict | None = None,
    max_tokens: int | None = None,
) -> str:
    """简单的非工具对话调用（用于代理思考等不需要 function_call 的场景）.

    Returns:
        模型输出的文本
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    resp = await forwarder.chat(
        role,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )
    return resp["choices"][0]["message"]["content"] or ""
