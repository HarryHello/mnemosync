"""LangGraph 节点实现.

每个节点是一个函数: 接收 state, 返回 state 的部分更新.
所有 LLM 调用统一走 ``MultiForwarder`` + ``RoleResolver``, 角色 → 模型由
``role_bindings`` 表决定, 节点内无任何硬编码模型.

v0.3.2 改进: 共享 store 通过 LangGraph ``config["configurable"]`` 传入,
避免每次节点执行新建 SQLite 连接. CLI 等无 config 的调用路径仍回退到
懒加载 (从 settings 构建临时 store), 保持向后兼容.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

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
from src.core.memory.audience import RetrievalContext
from src.core.memory.trigger_reason import infer_trigger_reason
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

from .state import AgentState

logger = logging.getLogger(__name__)


def _get_stores(config: RunnableConfig | None) -> dict[str, Any]:
    """从 LangGraph config 中提取共享 store 实例.

    config["configurable"] 中可能包含的键:
      - multi_forwarder: MultiForwarder
      - resolver: RoleResolver
      - memory_store: SqliteMemoryStore
      - vector_store: VectorStore
      - notification_store: NotificationStore

    服务器模式下这些由 lifespan 注入 config, 共享长连接;
    CLI/测试模式下未提供时回退到懒加载 (临时构建短连接, 用完关闭).
    """
    configurable = (config or {}).get("configurable", {}) if config else {}
    stores: dict[str, Any] = dict(configurable)

    if "multi_forwarder" not in stores:
        stores["multi_forwarder"] = _make_multi_forwarder()
        stores["_owns_forwarder"] = True
    if "resolver" not in stores:
        _, stores["resolver"] = _make_multi_forwarder_with_resolver()
    if "memory_store" not in stores:
        s = get_settings()
        stores["memory_store"] = SqliteMemoryStore(str(s.storage.memory_db_abs))
    if "vector_store" not in stores:
        s = get_settings()
        stores["vector_store"] = VectorStore(str(s.storage.chroma_dir_abs))
    if "notification_store" not in stores:
        try:
            s = get_settings()
            stores["notification_store"] = NotificationStore(
                str(s.storage.notification_db_abs),
            )
        except (AttributeError, Exception):
            # 测试环境可能 mock 了 settings 但不含 notification_db_abs;
            # 不使用通知的节点不受影响
            stores["notification_store"] = None

    return stores


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


def _retrieval_context(state: AgentState, rel=None) -> RetrievalContext:
    """从图状态构建受众上下文 (v0.3.0).

    rel 传入时用于 FRIENDS_ONLY / CONFIDENTIAL 门槛判定; 调用方应在检索前
    先加载关系 (一次索引查询, 开销可忽略)。
    """
    return RetrievalContext(
        effective_user_id=state.get("source_user") or None,
        actor_id=state.get("actor_id"),
        space_id=state.get("space_id"),
        channel_type=state.get("channel_type"),
        relationship=rel,
    )


def _make_multi_forwarder_with_resolver() -> tuple[MultiForwarder, RoleResolver]:
    """构建 MultiForwarder + resolver 对 (共享同一 store)."""
    from src.core.config import get_settings
    store = LLMServiceStore(str(get_settings().storage.llm_db_abs))
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
    source_user = state.get("source_user") or ""

    extracted = state.get("extracted_new")
    if extracted is None:
        extracted = [m for m in messages if m.get("role") == "user"]

    return {"extracted_new": extracted, "source_user": source_user}


async def proxy_thinking_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """代理思考 Agent (CoT, 可选)."""
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
            rel = await memory_store.get_relationship(state["persona_id"], source_user)
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
        user_msg = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        logger.debug("  💬 用户消息: %s", user_msg[:100] if user_msg else "(空)")

        logger.debug("  🚀 调用代理思考 Agent...")
        result = await run_proxy_thinking(
            forwarder=forwarder,
            user_name=state.get("current_speaker") or "未知参与者",
            relationship=format_relationship(rel),
            memories=memories_text,
            user_message=user_msg,
            tools=None,
            channel_type=state.get("channel_type"),
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


async def main_dialogue_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
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
    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    vector_store: VectorStore = stores["vector_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("🤖 [main_dialogue] 开始处理")
    logger.debug("  source_user: %s", source_user)

    try:
        from src.core.memory.audience import AudienceFilter

        rel = await memory_store.get_relationship(state["persona_id"], source_user) if source_user else None
        logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")
        retrieval_ctx = _retrieval_context(state, rel)

        perms = await memory_store.list_permanent(
            source_user or None,
            limit=settings.memory.permanent_load_top,
            space_id=state.get("space_id"),
        )
        perms = AudienceFilter.filter(perms, retrieval_ctx)
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
                retrieval_ctx=retrieval_ctx,
            )
            logger.debug("  🔍 检索结果: %d 条", len(results))
            for r in results:
                await memory_store.mark_accessed(r.memory_id)
                entry = await memory_store.get_by_id(r.memory_id)
                if entry:
                    retrieved_entries.append(entry)

        # 情绪分析: 预计算一次, 供 memory_analysis + relationship_analysis 共享
        emotion_analysis = await _compute_emotion(forwarder, extracted)
        logger.debug("  💭 情绪分析: %s (强度=%.2f)", emotion_analysis.get("emotion", "?"), emotion_analysis.get("intensity", 0))

        conversation_history = state.get("messages", [])
        conversation_history = [m for m in conversation_history if m.get("role") != "system"]

        # 推断本轮触发原因（不需要客户端修改）
        extracted_user = ""
        for m in reversed(extracted):
            if m.get("role") == "user":
                extracted_user = m.get("content", "")
                break
        trigger = infer_trigger_reason(
            state.get("current_speaker"),
            extracted_user,
            channel_type=state.get("channel_type"),
        )

        # 调试事件: 触发原因
        from src.infra.debug_context import emit_pipeline
        emit_pipeline(
            (config or {}).get("configurable", {}).get("debug_bus") if config else None,
            event_kind="trigger_reason",
            reason=trigger,
            channel_type=state.get("channel_type"),
        )

        # 加载 Lorebook 条目 (关键词匹配)
        lorebook_entries: list = []
        lorebook_store = stores.get("lorebook_store")
        if lorebook_store is not None and query:
            try:
                lorebook_entries = await lorebook_store.match_for_space(
                    query, space_id=state.get("space_id"), limit=5,
                )
            except Exception:
                pass

        messages = build_main_dialogue_messages(
            persona_prompt=state.get("persona") or settings.persona.prompt,
            persona_name=state.get("persona_name") or settings.persona.name,
            user_name=state.get("current_speaker") or "未知参与者",
            permanent_memories=perms,
            retrieved_memories=retrieved_entries,
            relationship=rel,
            conversation_history=conversation_history,
            proxy_thinking_result=state.get("proxy_thinking_result"),
            current_speaker=state.get("current_speaker"),
            channel_type=state.get("channel_type"),
            space_label=state.get("space_id"),
            active_participants=state.get("active_participants"),
            trigger_reason=trigger,
            tools=state.get("tools"),
            persona_definition=state.get("persona_definition"),
            space_id=state.get("space_id"),
            lorebook_entries=lorebook_entries,
        )

        logger.debug("  📝 拼装消息数: %d", len(messages))
        logger.debug("  🚀 调用 LLM 生成回复...")
        dialogue = await run_main_dialogue(
            forwarder,
            messages,
            tools=state.get("tools"),
            tool_choice=state.get("tool_choice"),
            parallel_tool_calls=state.get("parallel_tool_calls"),
        )

        # 内部 tool 拦截: 模型调用了内部 tool 时, 服务端执行, 再调一轮 LLM
        internal_names: set[str] = state.get("internal_tool_names") or set()
        if dialogue.finish_reason == "tool_calls" and internal_names:
            import json as _json
            from src.core.tools.internal_registry import get_internal_tool_registry

            registry = get_internal_tool_registry()
            tool_calls = dialogue.message.get("tool_calls") or []
            internal_calls = [
                tc for tc in tool_calls
                if tc.get("function", {}).get("name") in internal_names
            ]
            client_calls = [
                tc for tc in tool_calls
                if tc.get("function", {}).get("name") not in internal_names
            ]

            if internal_calls:
                logger.debug("  🔧 内部 tool 拦截: %d 个", len(internal_calls))
                # 执行内部 tool, 构建 tool_result
                messages_with_tools = list(messages) + [dict(dialogue.message)]
                identity_store = stores.get("identity_store")
                for tc in internal_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    handler_tool = registry.get(tool_name)
                    if handler_tool is None:
                        continue
                    # 解析参数
                    try:
                        args = _json.loads(func.get("arguments") or "{}")
                    except _json.JSONDecodeError:
                        args = {}
                    # 执行 handler
                    try:
                        result = await handler_tool.handler(
                            actor_id=state.get("actor_id"),
                            space_id=state.get("space_id"),
                            display_name=state.get("current_speaker"),
                            identity_store=identity_store,
                            **args,
                        )
                    except Exception as e:
                        result = {"success": False, "error": str(e)}
                    messages_with_tools.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": _json.dumps(result, ensure_ascii=False),
                    })
                    logger.debug("  🔧 内部 tool %s 结果: %s", tool_name, result)

                # 再调一轮 LLM, 让模型基于 tool_result 生成自然回复
                logger.debug("  🚀 内部 tool 执行完毕, 再调 LLM...")
                dialogue = await run_main_dialogue(
                    forwarder,
                    messages_with_tools,
                    tools=state.get("tools"),
                    tool_choice=None,  # 第二轮不强制工具
                    parallel_tool_calls=state.get("parallel_tool_calls"),
                )
                # 第二轮如果仍有内部 tool_calls, 放弃拦截直接返回 (防死循环)
                # 合并第一轮的客户端 tool_calls (如果有)
                second_tool_calls = dialogue.message.get("tool_calls") or []
                if client_calls and not second_tool_calls:
                    dialogue.message["tool_calls"] = client_calls
                    dialogue.finish_reason = "tool_calls"

        content = dialogue.message.get("content")
        response = content if isinstance(content, str) else ""

        # Expressor 表达改写 (仅群聊最终文本, 不改写工具调用)
        if (
            dialogue.finish_reason == "stop"
            and response
            and state.get("channel_type") == "group"
        ):
            from src.core.agents import ExpressorConfig, run_expressor

            expressor_cfg = ExpressorConfig(enabled=True)
            relationship_summary = format_relationship(rel)
            expression_style = state.get("expression_style", "")
            rewritten = await run_expressor(
                forwarder,
                response,
                state.get("current_speaker") or "未知参与者",
                state.get("channel_type"),
                relationship_summary,
                config=expressor_cfg,
                expression_style=expression_style,
            )
            if rewritten != response:
                logger.debug(
                    "  ✨ Expressor 改写: %d → %d",
                    len(response), len(rewritten),
                )
                # 调试事件: Expressor 改写对比
                from src.infra.debug_context import emit_pipeline
                emit_pipeline(
                    (config or {}).get("configurable", {}).get("debug_bus") if config else None,
                    event_kind="expressor_rewrite",
                    original_length=len(response),
                    rewritten_length=len(rewritten),
                    original_preview=response[:200],
                    rewritten_preview=rewritten[:200],
                    expression_style=expression_style or None,
                )
                response = rewritten
                dialogue.message["content"] = rewritten

        logger.debug(
            "  ✅ 生成完成, 长度: %d, finish_reason: %s",
            len(response),
            dialogue.finish_reason,
        )
        result: dict[str, Any] = {
            "response": response,
            "response_message": dialogue.message,
            "finish_reason": dialogue.finish_reason,
            "emotion_analysis": emotion_analysis,
        }
        if dialogue.usage:
            result["upstream_usage"] = dialogue.usage
        return result
    finally:
        if owns_fwd:
            await forwarder.close()


async def memory_analysis_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """记忆分析 Agent: ReAct, 提取候选记忆. 衰减由确定性公式处理."""
    if state.get("finish_reason") == "tool_calls":
        logger.debug("🧠 [memory_analysis] 工具中间轮, 跳过")
        return {"new_memories": [], "decay_evaluations": []}

    settings = get_settings()
    source_user = state["source_user"]
    # 非归属模式: 无有效用户, 不写入任何私有记忆
    if not source_user:
        logger.debug("🧠 [memory_analysis] 非归属模式, 跳过")
        return {"new_memories": [], "decay_evaluations": []}

    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    resolver: RoleResolver = stores["resolver"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    vector_store: VectorStore = stores["vector_store"]
    notification_store: NotificationStore = stores["notification_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("🧠 [memory_analysis] 开始处理")

    try:
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"new_memories": [], "decay_evaluations": []}

        rel_for_addressing = await memory_store.get_relationship(state["persona_id"], source_user)
        persona_addr, user_addr, rel_ctx = _resolve_addressing(rel_for_addressing, settings)

        # 受众上下文: 记忆分析 Agent 的查重检索也按当前会话受众过滤
        tools = [
            make_vector_search_tool(retriever, _retrieval_context(state, rel_for_addressing)),
        ]

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
            current_speaker=state.get("current_speaker") or "未知参与者",
            channel_type=state.get("channel_type"),
        )

        logger.debug("  ✅ 记忆分析完成: 新记忆 %d 条", len(out.new_memories))

        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder, resolver=resolver)
        lifecycle.notification_store = notification_store
        for cand in out.new_memories:
            await lifecycle.store_candidate(
                cand, source_user=source_user, space_id=state.get("space_id"),
            )

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
        if owns_fwd:
            await forwarder.close()


async def relationship_analysis_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """关系分析 Agent: CoT, 计算亲密度增量."""
    if state.get("finish_reason") == "tool_calls":
        logger.debug("💝 [relationship_analysis] 工具中间轮, 跳过")
        return {"relationship_delta": {}}

    settings = get_settings()
    source_user = state["source_user"]
    # 非归属模式: 无有效用户, 不更新关系
    if not source_user:
        logger.debug("💝 [relationship_analysis] 非归属模式, 跳过")
        return {"relationship_delta": {}}

    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("💝 [relationship_analysis] 开始处理")

    try:
        rel = await memory_store.get_relationship(state["persona_id"], source_user)
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
                make_update_addressing_tool(
                    memory_store, state["persona_id"], source_user,
                    actor_id=state.get("actor_id"),
                ),
            ],
            max_iterations=2,
            persona_name=settings.persona.name,
            persona_addressing=persona_addr,
            user_addressing=user_addr,
            relation_context=rel_ctx,
            emotion_analysis=emotion_text,
            current_speaker=state.get("current_speaker") or "未知参与者",
            channel_type=state.get("channel_type"),
        )

        logger.debug("  ✅ 关系分析完成: 亲密 %+.2f, 信任 %+.2f",
                     out.intimacy_delta, out.trust_delta)

        lifecycle = MemoryLifecycle(memory_store, None, forwarder)  # type: ignore[arg-type]
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
