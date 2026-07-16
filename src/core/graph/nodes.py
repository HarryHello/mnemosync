"""LangGraph 节点实现.

每个节点是一个函数: 接收 state, 返回 state 的部分更新.
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
from src.infra import Forwarder, ForwarderConfig
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


def _make_forwarder() -> Forwarder:
    s = get_settings()
    return Forwarder(ForwarderConfig(
        base_url=s.chat.base_url, api_key=s.chat.api_key,
        default_model=s.chat.main_model, timeout=90.0,
    ))


async def parse_request_node(state: AgentState) -> dict[str, Any]:
    """消息提取 + 用户标识解析.

    本节点期望由 API 层提前填好 source_user/messages/persona 等,
    此处仅做消息提取（从 messages 中去掉已存储的历史）.
    简化: 当 extracted_new 未提供时, 取 messages 最后一条 user 消息.
    """
    messages = state.get("messages", [])
    source_user = state.get("source_user", "default")

    # 消息提取: 若上层已提供 extracted_new 则直接用; 否则取所有 user 消息作为新内容
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
    forwarder = _make_forwarder()
    try:
        # 加载永久记忆供代理思考参考
        memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
        await memory_store.init_db()
        perms = await memory_store.list_permanent(state["source_user"], limit=5)
        memories_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
        logger.debug("  📚 参考记忆: %d 条", len(perms))

        # 最新用户消息
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
    forwarder = _make_forwarder()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    logger.debug("=" * 60)
    logger.debug("🤖 [main_dialogue] 开始处理")
    logger.debug("  source_user: %s", source_user)

    try:
        # 1. 加载永久记忆
        perms = await memory_store.list_permanent(
            source_user, limit=settings.memory.permanent_load_top
        )
        logger.debug("  📚 永久记忆: %d 条", len(perms))
        for i, m in enumerate(perms):
            logger.debug("    [%d] %s", i, m.content[:50] if m.content else "")

        # 2. 语义检索相关记忆
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
            # 标记访问
            for r in results:
                await memory_store.mark_accessed(r.memory_id)
                entry = await memory_store.get_by_id(r.memory_id)
                if entry:
                    retrieved_entries.append(entry)
                    logger.debug("    - %s (score: %.3f)", entry.content[:50] if entry.content else "", r.score)

        # 3. 加载关系状态
        rel = await memory_store.get_relationship("default", source_user)
        logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")

        # 4. 拼装上下文
        conversation_history = state.get("messages", [])
        # 去掉原始 system（如果有）, 用我们的拼装
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
        for i, msg in enumerate(messages):
            content = msg.get("content", "")[:80] if msg.get("content") else ""
            logger.debug("    [%d] %s: %s...", i, msg.get("role"), content)

        # 5. 生成回复 (流式由 API 层直接处理, 此处走非流式)
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
    forwarder = _make_forwarder()
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

        # 构建对话文本
        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"new_memories": [], "decay_evaluations": []}

        logger.debug("  💬 对话内容: %s", conversation[:100] if len(conversation) > 100 else conversation)

        # 待评估的已有记忆（取一批普通记忆）
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

        logger.debug("  ✅ 记忆分析完成")
        logger.debug("    新记忆: %d 条", len(out.new_memories))
        logger.debug("    衰减评估: %d 条", len(out.decay_evaluations))

        # 持久化
        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder)
        for cand in out.new_memories:
            await lifecycle.store_candidate(cand, source_user=source_user)
            logger.debug("    📝 新记忆: %s", cand.content[:50] if cand.content else "")
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
    forwarder = _make_forwarder()
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

        logger.debug("  💬 对话内容: %s", conversation[:100] if len(conversation) > 100 else conversation)

        logger.debug("  🚀 调用关系分析 Agent...")
        out = await run_relationship_analysis(
            forwarder=forwarder,
            current_relationship=current_rel_str,
            conversation=conversation,
            tools=[make_emotion_analyzer_tool(forwarder)],
            max_iterations=3,
        )

        logger.debug("  ✅ 关系分析完成")
        logger.debug("    亲密度变化: %+.2f", out.intimacy_delta)
        logger.debug("    信任度变化: %+.2f", out.trust_delta)
        logger.debug("    新关系类型: %s", out.new_relationship_type or "(不变)")
        logger.debug("    备注: %s", out.notes or "(无)")

        # 持久化关系更新
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
