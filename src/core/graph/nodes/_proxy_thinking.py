"""Proxy thinking node: optional Chain-of-Thought agent."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.core.agents import run_proxy_thinking
from src.core.agents.tracking import run_agent_tracked
from src.core.graph.state import AgentState
from src.core.memory import format_relationship
from src.core.utils import last_user_message
from src.infra.forwarder.multi import MultiForwarder
from src.persistence.memory_store import SqliteMemoryStore

from ._helpers import _retrieval_context

logger = logging.getLogger(__name__)


async def proxy_thinking_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Proxy thinking agent (CoT, optional)."""
    from src.core.graph.nodes import _get_stores

    if not state.get("proxy_thinking_enabled"):
        return {}

    logger.debug("=" * 60)
    logger.debug("🤔 [proxy_thinking] 开始处理")

    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    owns_fwd = stores.get("_owns_forwarder", False)
    try:
        source_user = state["source_user"]
        if source_user:
            rel = await stores["relationship_store"].get_relationship(state["persona_id"], source_user)
            perms = await memory_store.list_permanent(
                source_user, limit=5, space_id=state.get("space_id"),
            )
            from src.core.memory.audience import AudienceFilter
            perms = AudienceFilter.filter(perms, _retrieval_context(state, rel))
        else:
            perms = []
            rel = None
        memories_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
        logger.debug("  📚 参考记忆: %d 条", len(perms))

        extracted = state.get("extracted_new", [])
        user_msg = last_user_message(extracted)

        logger.debug("  💬 用户消息: %s", user_msg[:100] if user_msg else "(空)")

        logger.debug("  🚀 调用代理思考 Agent...")
        result = await run_agent_tracked(
            "proxy_thinking",
            run_proxy_thinking(
                forwarder=forwarder,
                user_name=state.get("current_speaker") or "未知参与者",
                relationship=format_relationship(rel),
                memories=memories_text,
                user_message=user_msg,
                tools=None,
                channel_type=state.get("channel_type"),
            ),
            store=stores.get("agent_run_store"),
            debug_bus=stores.get("debug_bus"),
            parent_request_id=state.get("interaction_id"),
        )
        logger.debug("  ✅ 代理思考完成")
        logger.debug("  📤 思考结果: %s", result[:100] if result else "(空)")
        return {"proxy_thinking_result": result}
    except Exception as e:
        logger.warning("代理思考失败, 退化为正常模式: %s", e)
        return {"errors": [f"proxy_thinking: {e}"]}
    finally:
        if owns_fwd:
            await forwarder.close()
