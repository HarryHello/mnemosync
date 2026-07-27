"""LangGraph 状态定义.

所有节点通过 AgentState 通信.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """编排状态.

    total=False 允许字段逐步填充, 节点只读取自己需要的字段.
    """

    # === 请求上下文（parse_request 写入） ===
    messages: list[dict[str, Any]]          # 原始 messages（OpenAI 格式）
    extracted_new: list[dict[str, Any]]     # 提取出的新内容
    tools: list[dict[str, Any]] | None        # 客户端本轮提供的工具, 仅 MAIN 可用
    tool_choice: str | dict[str, Any] | None
    parallel_tool_calls: bool | None
    source_user: str                        # 有效用户 ID (effective_user_id)
    current_speaker: str | None             # 模型可读的当前发言者身份（不得使用内部 UUID）
    active_participants: list[str]           # 当前短期窗口中的模型可读参与者标签
    actor_id: str | None                    # 当前 Actor ID (v0.3.0)
    persona: str                            # 人格 system prompt
    persona_name: str                       # 人格名称
    persona_id: str                         # 人格标识 (v0.3.0 仍为 "default", 不再硬编码)
    thread_id: str                          # 会话 ID（checkpoint 用）
    proxy_thinking_enabled: bool            # 是否启用代理思考
    space_id: str | None                    # 会话空间 ID (v0.3.0)
    channel_type: str | None                # "direct" | "group" | None

    # === 代理推理 (proxy_thinking 写入) ===
    proxy_thinking_result: str | None

    # === 提示词清洗 (API 层写入, 来自 run_prompt_cleaning) ===
    prompt_cleaning_result: dict[str, Any]

    # === 检索结果（main_dialogue 内部, 不必入 state） ===
    # retrieved_memories / permanent_memories 由 main_dialogue 节点内部处理

    # === 情绪分析（main_dialogue 计算, 供 memory_analysis + relationship_analysis 共享） ===
    emotion_analysis: dict[str, Any]           # 预计算的情绪分析结果, 含 emotion/intensity/category/keywords/summary

    # === 主对话输出（main_dialogue 写入） ===
    response: str                           # 最终用户可见文本; 纯工具调用时为空串
    response_message: dict[str, Any]        # 完整 assistant message (含 tool_calls)
    finish_reason: str | None               # stop / length / tool_calls / ...
    response_chunks: list[bytes]            # 流式响应收集的 chunks（供异步存储）
    upstream_usage: dict[str, Any]          # 上游原样返回的 usage 字典 (prompt/completion/total_tokens)

    # === 记忆分析输出（memory_analysis 写入） ===
    new_memories: list[dict[str, Any]]
    decay_evaluations: list[dict[str, Any]]
    decay_targets: list[dict[str, Any]]     # 待评估的已有记忆

    # === 关系分析输出（relationship_analysis 写入） ===
    relationship_delta: dict[str, Any]

    # === 全局 ===
    errors: list[str]
    stream_mode: bool                       # 是否流式响应
