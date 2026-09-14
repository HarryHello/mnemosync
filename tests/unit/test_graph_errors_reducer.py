"""AgentState.errors reducer 回归测试 (v0.4.1-beta.13).

回归: memory_analysis 与 relationship_analysis 并行运行, 双双失败时各自
返回 {"errors": [...]}, errors 无 reducer 时 LangGraph 抛
INVALID_CONCURRENT_GRAPH_UPDATE, 整个请求 500 (AstrBot 带图请求实测).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class _ParallelState(TypedDict):
    """模拟 AgentState 的 errors 键形态 (Annotated + operator.add)."""

    errors: Annotated[list[str], operator.add]


def _fail_a(state: _ParallelState) -> dict:
    return {"errors": ["memory_analysis: boom"]}


def _fail_b(state: _ParallelState) -> dict:
    return {"errors": ["relationship_analysis: boom"]}


def test_parallel_error_writes_merge() -> None:
    """两个并行节点同时写 errors 必须 merge 而不是抛并发更新异常."""
    graph = StateGraph(_ParallelState)
    graph.add_node("a", _fail_a)
    graph.add_node("b", _fail_b)
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)
    app = graph.compile()

    result = app.invoke({"errors": []})
    assert sorted(result["errors"]) == [
        "memory_analysis: boom",
        "relationship_analysis: boom",
    ]


def test_agent_state_errors_has_reducer() -> None:
    """真实 AgentState.errors 必须带 operator.add reducer."""
    import typing

    from src.core.graph.state import AgentState

    # state.py 开启了 from __future__ import annotations, 注解是字符串,
    # 需 get_type_hints 解析 (LangGraph 内部同样如此); include_extras 保留 Annotated
    hints = typing.get_type_hints(AgentState, include_extras=True)
    errors_hint = hints.get("errors")
    assert errors_hint is not None
    meta = getattr(errors_hint, "__metadata__", ())
    assert operator.add in meta, f"errors 缺少 reducer: {errors_hint!r}"
