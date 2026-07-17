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
from src.tools import (
    MemoryRetriever,
    make_emotion_analyzer_tool,
    make_time_decay_calculator_tool,
    make_vector_search_tool,
)

from .state import AgentState

logger = logging.getLogger(__name__)

# 与 src.api.lifespan 保持一致的默认路径 (相对项目根)
_LLM_SERVICE_DB_PATH = "data/llm_service.db"


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
        result: dict[str, Any] = {"response": response}
        if usage:
            result["upstream_usage"] = usage
        return result
    finally:
        await forwarder.close()


async def memory_analysis_node(state: AgentState) -> dict[str, Any]:
    """记忆分析 Agent: ReAct, 提取候选记忆 + 衰减评估."""
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
            make_emotion_analyzer_tool(forwarder),
            make_time_decay_calculator_tool(memory_store),
        ]

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"new_memories": [], "decay_evaluations": []}

        decay_targets_entries = await memory_store.list_for_decay(skip_hours=24, limit=10)
        decay_targets = [
            {
                "memory_id": e.id,
                "content": e.content,
                "importance": e.importance,
                "decay_rate": e.decay_rate,
                "memory_type": e.memory_type.value,
            }
            for e in decay_targets_entries
        ]
        logger.debug("  📉 待衰减评估: %d 条", len(decay_targets))

        logger.debug("  🚀 调用记忆分析 Agent...")
        out = await run_memory_analysis(
            forwarder=forwarder,
            source_user=source_user,
            conversation=conversation,
            tools=tools,
            decay_targets=decay_targets if decay_targets else None,
            max_iterations=6,
        )

        logger.debug("  ✅ 记忆分析完成: 新记忆 %d 条, 衰减评估 %d 条",
                     len(out.new_memories), len(out.decay_evaluations))

        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder, resolver=resolver)
        for cand in out.new_memories:
            await lifecycle.store_candidate(cand, source_user=source_user)
        await lifecycle.apply_decay_evaluations(out.decay_evaluations)

        return {
            "new_memories": [
                {
                    "content": m.content, "memory_type": m.memory_type.value,
                    "importance": m.importance, "reasoning": m.reasoning,
                }
                for m in out.new_memories
            ],
            "decay_evaluations": [e.__dict__ for e in out.decay_evaluations],
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

        logger.debug("  🚀 调用关系分析 Agent...")
        out = await run_relationship_analysis(
            forwarder=forwarder,
            current_relationship=current_rel_str,
            conversation=conversation,
            tools=[make_emotion_analyzer_tool(forwarder)],
            max_iterations=3,
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
