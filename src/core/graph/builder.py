"""LangGraph StateGraph 构建与编译.

流程:
    parse_request → main_dialogue
    → [relationship_analysis ∥ memory_analysis] → writeback → END

各节点通过 AgentState (TypedDict) 通信.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .nodes import (
    main_dialogue_node,
    memory_analysis_node,
    parse_request_node,
    relationship_analysis_node,
)
from .state import AgentState

# ── 路由函数 ──────────────────────────────────────────────────


def _route_after_main_dialogue(state: AgentState) -> str:
    """main_dialogue 完成后, 检查是否需要关系分析.

    关系分析需要对话内容, 但主对话节点已经完成.
    memory_analysis 总是运行.
    """
    return "relationship_analysis"


# ── 图构建 ─────────────────────────────────────────────────────


def build_graph() -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
    """构建并返回编译好的 LangGraph 图.

    Returns:
        编译好的 StateGraph (可通过 .ainvoke() 调用)
    """
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    graph.add_node("parse_request", parse_request_node)
    graph.add_node("main_dialogue", main_dialogue_node)
    graph.add_node("relationship_analysis", relationship_analysis_node)
    graph.add_node("memory_analysis", memory_analysis_node)

    # ── 入口 ──
    graph.set_entry_point("parse_request")

    # ── 顺序边 ──
    graph.add_edge("parse_request", "main_dialogue")

    # ── 并行分支: main_dialogue → relationship_analysis + memory_analysis ──
    graph.add_edge("main_dialogue", "relationship_analysis")
    graph.add_edge("main_dialogue", "memory_analysis")

    # ── 汇合: 关系分析 + 记忆分析 → END ──
    graph.add_edge("relationship_analysis", END)
    graph.add_edge("memory_analysis", END)

    return graph.compile()


# 便于类型提示
CompiledGraph = CompiledStateGraph[AgentState, Any, AgentState, AgentState]
