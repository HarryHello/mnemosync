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
    source_user: str                        # 来源用户标识
    persona: str                            # 人格 system prompt
    persona_name: str                       # 人格名称
    thread_id: str                          # 会话 ID（checkpoint 用）
    proxy_thinking_enabled: bool            # 是否启用代理思考

    # === 代理思考（proxy_thinking 写入） ===
    proxy_thinking_result: str | None

    # === 检索结果（main_dialogue 内部, 不必入 state） ===
    # retrieved_memories / permanent_memories 由 main_dialogue 节点内部处理

    # === 主对话输出（main_dialogue 写入） ===
    response: str                           # 生成的回复
    response_chunks: list[bytes]            # 流式响应收集的 chunks（供异步存储）

    # === 记忆分析输出（memory_analysis 写入） ===
    new_memories: list[dict[str, Any]]
    decay_evaluations: list[dict[str, Any]]
    decay_targets: list[dict[str, Any]]     # 待评估的已有记忆

    # === 关系分析输出（relationship_analysis 写入） ===
    relationship_delta: dict[str, Any]

    # === 全局 ===
    errors: list[str]
    stream_mode: bool                       # 是否流式响应
