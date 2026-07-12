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

    settings = get_settings()
    forwarder = _make_forwarder()
    try:
        # 加载永久记忆供代理思考参考
        memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
        await memory_store.init_db()
        perms = await memory_store.list_permanent(state["source_user"], limit=5)
        memories_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"

        # 最新用户消息
        extracted = state.get("extracted_new", [])
        user_msg = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        rel = await memory_store.get_relationship("default", state["source_user"])
        result = await run_proxy_thinking(
            forwarder=forwarder,
            user_name=state["source_user"],
            relationship=format_relationship(rel),
            memories=memories_text,
            user_message=user_msg,
            tools=None,
        )
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

    try:
        # 1. 加载永久记忆
        perms = await memory_store.list_permanent(
            source_user, limit=settings.memory.permanent_load_top
        )

        # 2. 语义检索相关记忆
        extracted = state.get("extracted_new", [])
        query = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                query = m.get("content", "")
                break

        retrieved_entries: list = []
        if query:
            retriever = MemoryRetriever(forwarder, vector_store, memory_store)
            results = await retriever.search(
                query, top_k=settings.memory.retrieval_top_k,
                source_user=source_user,
            )
            # 标记访问
            for r in results:
                await memory_store.mark_accessed(r.memory_id)
                entry = await memory_store.get_by_id(r.memory_id)
                if entry:
                    retrieved_entries.append(entry)

        # 3. 加载关系状态
        rel = await memory_store.get_relationship("default", source_user)

        # 4. 拼装上下文
        conversation_history = state.get("messages", [])
        # 去掉原始 system（如果有）, 用我们的拼装
        conversation_history = [m for m in conversation_history if m.get("role") != "system"]

        messages = build_main_dialogue_messages(
            persona_prompt=state.get("persona", "你是一个温暖、有记忆能力的 AI 助手。"),
            persona_name=state.get("persona_name", "助手"),
            user_name=source_user,
            permanent_memories=perms,
            retrieved_memories=retrieved_entries,
            relationship=rel,
            conversation_history=conversation_history,
            proxy_thinking_result=state.get("proxy_thinking_result"),
        )

        # 5. 生成回复 (流式由 API 层直接处理, 此处走非流式)
        response = await run_main_dialogue(forwarder, messages)
        return {"response": response}
    finally:
        await forwarder.close()


async def memory_analysis_node(state: AgentState) -> dict[str, Any]:
    """记忆分析 Agent: ReAct, 提取候选记忆 + 衰减评估."""
    settings = get_settings()
    source_user = state["source_user"]
    forwarder = _make_forwarder()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()

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
            return {"new_memories": [], "decay_evaluations": []}

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

        out = await run_memory_analysis(
            forwarder=forwarder,
            source_user=source_user,
            conversation=conversation,
            tools=tools,
            decay_targets=decay_targets if decay_targets else None,
            max_iterations=6,
        )

        # 持久化
        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder)
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
    forwarder = _make_forwarder()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()

    try:
        source_user = state["source_user"]
        rel = await memory_store.get_relationship("default", source_user)
        current_rel_str = format_relationship(rel)

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            return {"relationship_delta": {}}

        out = await run_relationship_analysis(
            forwarder=forwarder,
            current_relationship=current_rel_str,
            conversation=conversation,
            tools=[make_emotion_analyzer_tool(forwarder)],
            max_iterations=3,
        )

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
