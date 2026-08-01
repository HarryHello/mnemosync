"""辅助 Agent 统一规格定义与注册表."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AgentSpec:
    """Agent 规格定义 — 不可变配置."""

    name: str
    purpose: str
    model_role: str  # "MAIN" | "ASSIST" (string 避免循环导入)
    runner_type: Literal["simple", "react"]
    allowed_tools: list[str] = field(default_factory=list)
    timeout_seconds: float = 30.0
    max_iterations: int = 1
    privacy_scope: str = "user"


# ── 注册表 ──────────────────────────────────────────────────────────────────

AGENT_SPECS: dict[str, AgentSpec] = {
    "prompt_cleaning": AgentSpec(
        name="prompt_cleaning",
        purpose="清洗客户端 system 消息",
        model_role="ASSIST",
        runner_type="simple",
        timeout_seconds=15,
        max_iterations=1,
    ),
    "expressor": AgentSpec(
        name="expressor",
        purpose="改写为自然群聊表达",
        model_role="ASSIST",
        runner_type="simple",
        timeout_seconds=10,
        max_iterations=1,
    ),
    "proxy_thinking": AgentSpec(
        name="proxy_thinking",
        purpose="生成代理推理",
        model_role="ASSIST",
        runner_type="simple",
        timeout_seconds=30,
        max_iterations=1,
    ),
    "memory_analysis": AgentSpec(
        name="memory_analysis",
        purpose="提取候选记忆",
        model_role="ASSIST",
        runner_type="react",
        allowed_tools=["vector_search"],
        timeout_seconds=60,
        max_iterations=4,
    ),
    "relationship_analysis": AgentSpec(
        name="relationship_analysis",
        purpose="计算关系增量",
        model_role="ASSIST",
        runner_type="react",
        allowed_tools=["update_addressing"],
        timeout_seconds=30,
        max_iterations=2,
    ),
}


def get_spec(name: str) -> AgentSpec:
    """按名称获取 AgentSpec, 未注册时抛 KeyError."""
    try:
        return AGENT_SPECS[name]
    except KeyError:
        raise KeyError(
            f"Unknown agent spec: {name!r}. "
            f"Available: {list(AGENT_SPECS)}"
        ) from None
