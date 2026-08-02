"""FastAPI 依赖注入: 从 app.state (AppState) 取共享的长连接 Store 单例.

所有 getter 通过 ``request.app.state`` 访问 AppState 数据类, 获得完整类型提示.
"""

from __future__ import annotations

from typing import cast

from fastapi import Request

from src.api.state import AppState
from src.core.memory.reindex import ReindexProgress
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.agent_run_store import AgentRunStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.idempotency_store import SqliteIdempotencyStore
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.lorebook_store import SqliteLorebookStore
from src.persistence.memory_store import SqliteMemoryStore, SqliteRelationshipStore
from src.persistence.notification_store import NotificationStore
from src.persistence.persona_store import SqlitePersonaStore
from src.persistence.space_policy_store import SqliteSpacePolicyStore


def _state(request: Request) -> AppState:
    """获取 AppState, 若未初始化则抛错."""
    state = request.app.state
    if not isinstance(state, AppState):
        raise RuntimeError(
            f"app.state 未初始化 (当前类型: {type(state).__name__}) — "
            "检查 lifespan 是否正确挂载了 AppState"
        )
    return state


def _state_or_none(request: Request) -> AppState | None:
    """获取 AppState, 未初始化时返回 None (供中间件等早期路径使用)."""
    state = request.app.state
    return state if isinstance(state, AppState) else None


def _require[T](state: AppState, field: str, typ: type[T]) -> T:
    """从 AppState 取字段, 若为 None 抛 RuntimeError."""
    val = getattr(state, field)
    if val is None:
        raise RuntimeError(f"AppState.{field} 未初始化")
    return cast(T, val)


def get_auth_store(request: Request) -> SqliteAuthStore:
    return _require(_state(request), "auth_store", SqliteAuthStore)


def get_api_key_store(request: Request) -> SqliteApiKeyStore:
    return _require(_state(request), "api_key_store", SqliteApiKeyStore)


def get_memory_store(request: Request) -> SqliteMemoryStore:
    return _require(_state(request), "memory_store", SqliteMemoryStore)


def get_relationship_store(request: Request) -> SqliteRelationshipStore:
    return _require(_state(request), "relationship_store", SqliteRelationshipStore)


def get_http_log_store(request: Request) -> HttpLogStore:
    return _require(_state(request), "http_log_store", HttpLogStore)


def get_llm_service_store(request: Request) -> LLMServiceStore:
    return _require(_state(request), "llm_service_store", LLMServiceStore)


def get_resolver(request: Request) -> RoleResolver:
    return _require(_state(request), "resolver", RoleResolver)


def get_multi_forwarder(request: Request) -> MultiForwarder:
    return _require(_state(request), "multi_forwarder", MultiForwarder)


def get_vector_store(request: Request) -> VectorStore:
    return _require(_state(request), "vector_store", VectorStore)


def get_reindex_progress(request: Request) -> ReindexProgress:
    return _require(_state(request), "reindex_progress", ReindexProgress)


def get_conversation_store(request: Request) -> SqliteConversationStore:
    return _require(_state(request), "conversation_store", SqliteConversationStore)


def get_notification_store(request: Request) -> NotificationStore:
    return _require(_state(request), "notification_store", NotificationStore)


def get_identity_store(request: Request) -> SqliteIdentityStore:
    return _require(_state(request), "identity_store", SqliteIdentityStore)


def get_idempotency_store(request: Request) -> SqliteIdempotencyStore:
    return _require(_state(request), "idempotency_store", SqliteIdempotencyStore)


def get_lorebook_store(request: Request) -> SqliteLorebookStore:
    return _require(_state(request), "lorebook_store", SqliteLorebookStore)


def get_space_policy_store(request: Request) -> SqliteSpacePolicyStore:
    return _require(_state(request), "space_policy_store", SqliteSpacePolicyStore)


def get_persona_store(request: Request) -> SqlitePersonaStore:
    return _require(_state(request), "persona_store", SqlitePersonaStore)


def get_agent_run_store(request: Request) -> AgentRunStore:
    return _require(_state(request), "agent_run_store", AgentRunStore)
