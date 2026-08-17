"""Relationship analysis node: CoT agent computing favor_delta (v0.4.1).

好感度 (favor) 增量可正可负, 慢热快冷 (alpha 预设); 显著负向时写
EPHEMERAL 情绪锚点 + 更新全局 mood. 旧 intimacy_delta 兼容读取 (T8)."""

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
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Relationship analysis agent: CoT, computes favor_delta (+ mood_anchor)."""
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
                        relationship_store,
                        state["persona_id"],
                        source_user,
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

        logger.debug("  ✅ 关系分析完成: 好感度 %+.2f", out.favor_delta)

        # v0.4.1: 应用人格的演进预设 (α_up/α_down), 慢热快冷
        from src.core.config import get_relationship_alpha

        persona_def = state.get("persona_definition")
        alpha_preset_id = (
            getattr(persona_def, "relationship_alpha", None) if persona_def is not None else None
        )
        alpha = get_relationship_alpha(alpha_preset_id, presets=settings.relationship_alpha)
        raw_delta = out.favor_delta
        eff_delta = raw_delta * (alpha.alpha_up if raw_delta >= 0 else alpha.alpha_down)

        lifecycle = MemoryLifecycle(
            memory_store, None, forwarder, relationship_store=relationship_store
        )
        await lifecycle.apply_relationship_update(
            persona_id=state["persona_id"],
            user_id=source_user,
            favor_delta=eff_delta,
            new_type=out.new_relationship_type,
            notes=out.notes,
        )

        # v0.4.1: 显著负面 → 写对象化情绪锚点 (EPHEMERAL 记忆, 脱敏, supersedes 旧锚点)
        anchor_written = False
        actor_id = state.get("actor_id")
        if raw_delta <= -0.15 and out.mood_anchor and actor_id:
            anchor_written = await _write_mood_anchor(
                memory_store=memory_store,
                persona_id=state["persona_id"],
                subject_actor_id=actor_id,
                content=out.mood_anchor,
                source_user=source_user,
                space_id=state.get("space_id"),
            )

        return {
            "relationship_delta": {
                "favor_delta": eff_delta,
                "raw_delta": raw_delta,
                "new_type": out.new_relationship_type,
                "notes": out.notes,
                "anchor_written": anchor_written,
            }
        }
    except Exception as e:
        logger.error("关系分析失败: %s", e)
        return {"errors": [f"relationship_analysis: {e}"]}
    finally:
        if owns_fwd:
            await forwarder.close()


async def _write_mood_anchor(
    *,
    memory_store: SqliteMemoryStore,
    persona_id: str,
    subject_actor_id: str,
    content: str,
    source_user: str,
    space_id: str | None,
) -> bool:
    """写入/更新人格对某人的对象化情绪锚点 (v0.4.1, RFC §6).

    - EPHEMERAL 类型 + 高衰减 (0.95 ≈ 2~3 天半衰期)
    - 同 subject 旧锚点 supersedes 标记 (去重)
    - 内容已由关系分析 Agent 按卫生规则脱敏 (只含"被谁/大致原因")
    """
    try:
        from src.core.memory.models import MemoryEntry, MemoryType

        old = await memory_store.list_ephemeral_by_subject(subject_actor_id, limit=1)

        entry = MemoryEntry.create(
            content=content,
            role="system",
            source_user=source_user,
            memory_type=MemoryType.EPHEMERAL,
            importance=0.8,
            decay_rate=0.95,
        )
        entry.subject_actor_id = subject_actor_id
        await memory_store.save(entry)
        if old:
            await memory_store.mark_superseded(old[0].id, entry.id)
        return True
    except Exception as e:
        logger.warning("写入情绪锚点失败: %s", e)
        return False
