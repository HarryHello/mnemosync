"""Relationship analysis node: CoT agent that computes intimacy delta."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.core.agents import run_relationship_analysis
from src.core.agents.spec import get_spec
from src.core.agents.tracking import run_agent_tracked
from src.core.config import get_settings
from src.core.graph.state import AgentState
from src.core.memory import MemoryLifecycle, format_relationship
from src.infra.forwarder.multi import MultiForwarder
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.relationship_store import SqliteRelationshipStore
from src.tools import make_update_addressing_tool

from ._helpers import _format_emotion_text, _resolve_addressing

logger = logging.getLogger(__name__)


async def relationship_analysis_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Relationship analysis agent: CoT, computes intimacy delta."""
    from src.core.graph.nodes import _get_stores

    if state.get("finish_reason") == "tool_calls":
        logger.debug("💝 [relationship_analysis] 工具中间轮, 跳过")
        return {"relationship_delta": {}}

    settings = get_settings()
    source_user = state["source_user"]
    # Non-attribution mode: no valid user, don't update relationship
    if not source_user:
        logger.debug("💝 [relationship_analysis] 非归属模式, 跳过")
        return {"relationship_delta": {}}

    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    relationship_store: SqliteRelationshipStore = stores["relationship_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("💝 [relationship_analysis] 开始处理")

    try:
        rel = await relationship_store.get_relationship(state["persona_id"], source_user)
        current_rel_str = format_relationship(rel)
        logger.debug("  当前关系: %s", current_rel_str if current_rel_str else "(无)")

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"relationship_delta": {}}

        persona_addr, user_addr, rel_ctx = _resolve_addressing(rel, settings)

        # Get pre-computed emotion analysis from state
        emotion_analysis = state.get("emotion_analysis", {})
        emotion_text = _format_emotion_text(emotion_analysis)

        logger.debug("  🚀 调用关系分析 Agent...")
        out = await run_agent_tracked(
            "relationship_analysis",
            run_relationship_analysis(
                forwarder=forwarder,
                current_relationship=current_rel_str,
                conversation=conversation,
                tools=[
                    make_update_addressing_tool(
                        relationship_store, state["persona_id"], source_user,
                        actor_id=state.get("actor_id"),
                    ),
                ],
                max_iterations=get_spec("relationship_analysis").max_iterations,
                persona_name=settings.persona.name,
                persona_addressing=persona_addr,
                user_addressing=user_addr,
                relation_context=rel_ctx,
                emotion_analysis=emotion_text,
                current_speaker=state.get("current_speaker") or "未知参与者",
                channel_type=state.get("channel_type"),
            ),
            store=stores.get("agent_run_store"),
            debug_bus=stores.get("debug_bus"),
            parent_request_id=state.get("interaction_id"),
        )

        logger.debug("  ✅ 关系分析完成: 亲密 %+.2f, 信任 %+.2f",
                     out.intimacy_delta, out.trust_delta)

        lifecycle = MemoryLifecycle(memory_store, None, forwarder, relationship_store=relationship_store)  # type: ignore[arg-type]
        await lifecycle.apply_relationship_update(
            persona_id=state["persona_id"],
            user_id=source_user,
            intimacy_delta=out.intimacy_delta,
            trust_delta=out.trust_delta,
            new_type=out.new_relationship_type,
            notes=out.notes,
        )

        return {
            "relationship_delta": {
                "intimacy_delta": out.intimacy_delta,
                "trust_delta": out.trust_delta,
                "new_type": out.new_relationship_type,
                "notes": out.notes,
            }
        }
    except Exception as e:
        logger.error("关系分析失败: %s", e)
        return {"errors": [f"relationship_analysis: {e}"]}
    finally:
        if owns_fwd:
            await forwarder.close()
