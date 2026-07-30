"""持久化助手: conversation events 写入."""
import json
import logging
from typing import Any

from src.core.identity.plugin import NormalizedEvent
from src.core.memory.short_term import token_count_for_storage
from src.persistence.conversation_store import (
    ConversationEvent,
    SqliteConversationStore,
    build_event_fingerprint,
)

logger = logging.getLogger(__name__)


def _conversation_events(
    normalized: list[NormalizedEvent],
    request_id: str,
) -> list[ConversationEvent]:
    """将插件事件转为带稳定指纹的存储事件."""
    events: list[ConversationEvent] = []
    for item in normalized:
        event = ConversationEvent(
            role=item.role,
            content=item.content,
            token_count=token_count_for_storage(item.content),
            source_frontend=item.source_frontend,
            ts=item.source_timestamp,
            actor_id=item.actor_id,
            effective_user_id=item.effective_user_id,
            display_name_snapshot=item.display_name,
            external_key_snapshot=item.external_key,
            space_id=item.space_id,
            external_event_id=item.external_event_id,
            origin=item.origin,
            request_id=request_id,
        )
        if item.origin == "history_snapshot" or item.external_event_id:
            event.event_fingerprint = build_event_fingerprint(event)
        events.append(event)
    return events


async def _persist_plugin_events(
    store: SqliteConversationStore,
    normalized: list[NormalizedEvent],
    request_id: str,
) -> None:
    """批量持久化插件事件；失败不阻断主回复."""
    if not normalized:
        return
    try:
        result = await store.append_events(_conversation_events(normalized, request_id))
        logger.debug(
            "  🧩 规范化事件写入: 新增 %d, 去重 %d",
            result.inserted,
            result.duplicates,
        )
    except Exception as exc:
        logger.warning("写入规范化 conversation events 失败 (不影响响应): %s", exc)


async def _persist_assistant_event(
    store: SqliteConversationStore,
    content: str,
    initial_state: dict[str, Any],
    request_id: str,
    *,
    response_message: dict[str, Any] | None = None,
) -> None:
    if not content and not (response_message and response_message.get("tool_calls")):
        return
    interaction_id = initial_state.get("interaction_id")
    # 持久化 tool_calls 为独立 tool_call 事件 (不混入自然语言流水)
    tool_calls = (response_message or {}).get("tool_calls")
    if tool_calls:
        for call in tool_calls:
            call_id = call.get("id", "")
            func = call.get("function", {})
            func_name = func.get("name", "") if isinstance(func, dict) else ""
            if not call_id or not func_name:
                continue
            await store.append(
                role="assistant",
                content=json.dumps(call, ensure_ascii=False),
                token_count=8,
                source_frontend="mnemosync",
                actor_id=None,
                effective_user_id=initial_state.get("source_user"),
                space_id=initial_state.get("space_id"),
                origin="assistant",
                request_id=request_id,
                interaction_id=interaction_id,
                event_type="tool_call",
                tool_call_id=call_id,
                tool_name=func_name,
            )
    # 文本内容作为 message 事件持久化
    if content:
        await store.append(
            role="assistant",
            content=content,
            token_count=token_count_for_storage(content),
            source_frontend="mnemosync",
            actor_id=None,
            effective_user_id=initial_state.get("source_user"),
            space_id=initial_state.get("space_id"),
            origin="assistant",
            request_id=request_id,
            interaction_id=interaction_id,
            event_type="message",
        )
