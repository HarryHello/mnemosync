"""LangGraph 节点实现.

每个节点是一个函数: 接收 state, 返回 state 的部分更新.
所有 LLM 调用统一走 ``MultiForwarder`` + ``RoleResolver``, 角色 → 模型由
``role_bindings`` 表决定, 节点内无任何硬编码模型.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.agents import (
    run_main_dialogue,
    run_memory_analysis,
    run_proxy_thinking,
    run_relationship_analysis,
)
from src.core.config import get_settings
from src.core.memory import (
    MemoryLifecycle,
    build_main_dialogue_messages,
    format_relationship,
)
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.tools import (
    MemoryRetriever,
    make_update_addressing_tool,
    make_vector_search_tool,
)
from src.tools.emotion_analyzer import analyze_emotion

from .state import AgentState

logger = logging.getLogger(__name__)

# 与 src.api.lifespan 保持一致的默认路径 (相对项目根)
_LLM_SERVICE_DB_PATH = "data/llm_service.db"
_NOTIFICATION_DB_PATH = "data/notifications.db"


def _resolve_addressing(rel, settings) -> tuple[str, str, str]:
    """运行时称呼解析: 表值 (非 None) 优先, 否则回退 TOML 基线.

    v0.2.10 起 relationships 表有 persona_addressing / user_addressing / context 三列.
    Agent 或人工可通过 update_addressing tool / PUT 端点修改这些字段; 未被覆盖时
    (NULL / rel is None) 沿用 settings.persona.relation.* 的安装基线.
    """
    base = settings.persona.relation
    if rel is None:
        return base.persona_addressing, base.user_addressing, base.context
    return (
        rel.persona_addressing or base.persona_addressing,
        rel.user_addressing or base.user_addressing,
        rel.context or base.context,
    )


def _make_multi_forwarder_with_resolver() -> tuple[MultiForwarder, RoleResolver]:
    """构建 MultiForwarder + resolver 对 (共享同一 store)."""
    store = LLMServiceStore(_LLM_SERVICE_DB_PATH)
    resolver = RoleResolver(store)
    return MultiForwarder(resolver), resolver


def _make_multi_forwarder() -> MultiForwarder:
    """构建独立的 MultiForwarder (每次节点执行创建一次).

    每次调用新建 store/resolver 实例, 靠 role_bindings 表读取最新绑定,
    与外部 CLI 调用/服务器调用共享同一 SQLite 文件.
    """
    fwd, _ = _make_multi_forwarder_with_resolver()
    return fwd


async def _compute_emotion(
    forwarder: MultiForwarder,
    extracted: list[dict],
) -> dict:
    """预计算情绪分析，供多个 Agent 共享."""
    text = ""
    for m in reversed(extracted):
        if m.get("role") == "user":
            text = m.get("content", "")
            break
    if not text:
        return {"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""}
    try:
        from src.tools.emotion_analyzer import analyze_emotion
        result = await analyze_emotion(forwarder, text)
        return result.to_dict()
    except Exception as e:
        logger.warning("情绪分析失败: %s", e)
        return {"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""}


async def parse_request_node(state: AgentState) -> dict[str, Any]:
    """消息提取 + 用户标识解析."""
    messages = state.get("messages", [])
    source_user = state.get("source_user", "default")

    extracted = state.get("extracted_new")
    if extracted is None:
        extracted = [m for m in messages if m.get("role") == "user"]

    return {"extracted_new": extracted, "source_user": source_user}


async def proxy_thinking_node(state: AgentState) -> dict[str, Any]:
    """代理思考 Agent (CoT, 可选)."""
    if not state.get("proxy_thinking_enabled"):
        return {}

    logger.debug("=" * 60)
    logger.debug("🤔 [proxy_thinking] 开始处理")

    settings = get_settings()
    forwarder = _make_multi_forwarder()
    try:
        memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
        await memory_store.init_db()
        perms = await memory_store.list_permanent(state["source_user"], limit=5)
        memories_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
        logger.debug("  📚 参考记忆: %d 条", len(perms))

        extracted = state.get("extracted_new", [])
        user_msg = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        logger.debug("  💬 用户消息: %s", user_msg[:100] if user_msg else "(空)")

        rel = await memory_store.get_relationship("default", state["source_user"])
        logger.debug("  🚀 调用代理思考 Agent...")
        result = await run_proxy_thinking(
            forwarder=forwarder,
            user_name=state["source_user"],
            relationship=format_relationship(rel),
            memories=memories_text,
            user_message=user_msg,
            tools=None,
        )
        logger.debug("  ✅ 代理思考完成")
        logger.debug("  📤 思考结果: %s", result[:100] if result else "(空)")
        return {"proxy_thinking_result": result}
    except Exception as e:
        logger.warning("代理思考失败, 退化为正常模式: %s", e)
        return {"errors": [f"proxy_thinking: {e}"]}
    finally:
        await forwarder.close()


async def main_dialogue_node(state: AgentState) -> dict[str, Any]:
    """主对话 Agent: 加载记忆 + 拼装上下文 + 生成回复."""
    # 流式模式下 _run_memory_graph 已预填充 response: 直接返回, 不重复调用 LLM
    if "response" in state and state["response"] is not None:
        logger.debug("=" * 60)
        logger.debug("🤖 [main_dialogue] 检测到预填充 response: 跳过 LLM 调用")
        result: dict[str, Any] = {"response": state["response"]}
        if "upstream_usage" in state and state["upstream_usage"] is not None:
            result["upstream_usage"] = state["upstream_usage"]
        return result

    settings = get_settings()
    source_user = state["source_user"]
    forwarder = _make_multi_forwarder()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    logger.debug("=" * 60)
    logger.debug("🤖 [main_dialogue] 开始处理")
    logger.debug("  source_user: %s", source_user)

    try:
        perms = await memory_store.list_permanent(
            source_user, limit=settings.memory.permanent_load_top
        )
        logger.debug("  📚 永久记忆: %d 条", len(perms))
        for p in perms:
            await memory_store.mark_accessed(p.id)

        extracted = state.get("extracted_new", [])
        query = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                query = m.get("content", "")
                break

        logger.debug("  🔍 检索查询: %s", query[:100] if query else "(空)")

        retrieved_entries: list = []
        if query:
            retriever = MemoryRetriever(forwarder, vector_store, memory_store)
            results = await retriever.search(
                query, top_k=settings.memory.retrieval_top_k,
                source_user=source_user,
            )
            logger.debug("  🔍 检索结果: %d 条", len(results))
            for r in results:
                await memory_store.mark_accessed(r.memory_id)
                entry = await memory_store.get_by_id(r.memory_id)
                if entry:
                    retrieved_entries.append(entry)

        rel = await memory_store.get_relationship("default", source_user)
        logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")

        # 情绪分析: 预计算一次, 供 memory_analysis + relationship_analysis 共享
        emotion_analysis = await _compute_emotion(forwarder, extracted)
        logger.debug("  💭 情绪分析: %s (强度=%.2f)", emotion_analysis.get("emotion", "?"), emotion_analysis.get("intensity", 0))

        conversation_history = state.get("messages", [])
        conversation_history = [m for m in conversation_history if m.get("role") != "system"]

        messages = build_main_dialogue_messages(
            persona_prompt=state.get("persona") or settings.persona.prompt,
            persona_name=state.get("persona_name") or settings.persona.name,
            user_name=source_user,
            permanent_memories=perms,
            retrieved_memories=retrieved_entries,
            relationship=rel,
            conversation_history=conversation_history,
            proxy_thinking_result=state.get("proxy_thinking_result"),
        )

        logger.debug("  📝 拼装消息数: %d", len(messages))
        logger.debug("  🚀 调用 LLM 生成回复...")
        response, usage = await run_main_dialogue(forwarder, messages)
        logger.debug("  ✅ 生成完成, 长度: %d", len(response) if response else 0)
        result: dict[str, Any] = {"response": response, "emotion_analysis": emotion_analysis}
        if usage:
            result["upstream_usage"] = usage
        return result
    finally:
        await forwarder.close()


async def memory_analysis_node(state: AgentState) -> dict[str, Any]:
    """记忆分析 Agent: ReAct, 提取候选记忆. 衰减由确定性公式处理."""
    settings = get_settings()
    source_user = state["source_user"]
    forwarder, resolver = _make_multi_forwarder_with_resolver()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()

    logger.debug("=" * 60)
    logger.debug("🧠 [memory_analysis] 开始处理")

    try:
        vector_store = VectorStore(str(settings.storage.chroma_dir_abs))
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)
        tools = [
            make_vector_search_tool(retriever),
        ]

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"new_memories": [], "decay_evaluations": []}

        rel_for_addressing = await memory_store.get_relationship("default", source_user)
        persona_addr, user_addr, rel_ctx = _resolve_addressing(rel_for_addressing, settings)

        # 从 state 获取预计算的情绪分析
        emotion_analysis = state.get("emotion_analysis", {})
        emotion_text = (
            f"情绪: {emotion_analysis.get('emotion', 'neutral')}, "
            f"强度: {emotion_analysis.get('intensity', 0.0):.2f}, "
            f"类别: {emotion_analysis.get('category', 'other')}"
        )

        logger.debug("  🚀 调用记忆分析 Agent...")
        out = await run_memory_analysis(
            forwarder=forwarder,
            source_user=source_user,
            conversation=conversation,
            tools=tools,
            max_iterations=4,
            persona_name=settings.persona.name,
            persona_addressing=persona_addr,
            user_addressing=user_addr,
            relation_context=rel_ctx,
            emotion_analysis=emotion_text,
        )
        )

        logger.debug("  ✅ 记忆分析完成: 新记忆 %d 条", len(out.new_memories))

        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder, resolver=resolver)
        notification_store = NotificationStore(_NOTIFICATION_DB_PATH)
        await notification_store.init_db()
        lifecycle.notification_store = notification_store
        for cand in out.new_memories:
            await lifecycle.store_candidate(cand, source_user=source_user)

        # 确定性衰减：公式批量更新所有 NORMAL 记忆
        await lifecycle.run_deterministic_decay()

        return {
            "new_memories": [
                {
                    "content": m.content, "memory_type": m.memory_type.value,
                    "importance": m.importance, "reasoning": m.reasoning,
                }
                for m in out.new_memories
            ],
            "decay_evaluations": [],
        }
    except Exception as e:
        logger.error("记忆分析失败: %s", e)
        return {"errors": [f"memory_analysis: {e}"]}
    finally:
        await forwarder.close()


async def relationship_analysis_node(state: AgentState) -> dict[str, Any]:
    """关系分析 Agent: CoT, 计算亲密度增量."""
    settings = get_settings()
    forwarder = _make_multi_forwarder()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()

    logger.debug("=" * 60)
    logger.debug("💝 [relationship_analysis] 开始处理")

    try:
        source_user = state["source_user"]
        rel = await memory_store.get_relationship("default", source_user)
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

        # 从 state 获取预计算的情绪分析
        emotion_analysis = state.get("emotion_analysis", {})
        emotion_text = (
            f"情绪: {emotion_analysis.get('emotion', 'neutral')}, "
            f"强度: {emotion_analysis.get('intensity', 0.0):.2f}, "
            f"类别: {emotion_analysis.get('category', 'other')}"
        )

        logger.debug("  🚀 调用关系分析 Agent...")
        out = await run_relationship_analysis(
            forwarder=forwarder,
            current_relationship=current_rel_str,
            conversation=conversation,
            tools=[
                make_update_addressing_tool(memory_store, "default", source_user),
            ],
            max_iterations=2,
            persona_name=settings.persona.name,
            persona_addressing=persona_addr,
            user_addressing=user_addr,
            relation_context=rel_ctx,
            emotion_analysis=emotion_text,
        )

        logger.debug("  ✅ 关系分析完成: 亲密 %+.2f, 信任 %+.2f",
                     out.intimacy_delta, out.trust_delta)

        lifecycle = MemoryLifecycle(memory_store, None, forwarder)  # type: ignore[arg-type]
        await lifecycle.apply_relationship_update(
            persona_id="default",
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
        await forwarder.close()
