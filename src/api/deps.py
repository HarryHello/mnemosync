"""FastAPI 依赖注入: 从 app.state 取共享的长连接 Store 单例."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from src.infra.llm_service.store import LLMServiceStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.idempotency_store import SqliteIdempotencyStore
from src.persistence.lorebook_store import SqliteLorebookStore
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.persistence.space_policy_store import SqliteSpacePolicyStore

if TYPE_CHECKING:
    from src.core.memory.reindex import ReindexProgress
    from src.core.models.resolver import RoleResolver
    from src.infra.forwarder.multi import MultiForwarder
    from src.infra.vector_store import VectorStore


def get_auth_store(request: Request) -> SqliteAuthStore:
    return request.app.state.auth_store


def get_api_key_store(request: Request) -> SqliteApiKeyStore:
    return request.app.state.api_key_store


def get_memory_store(request: Request) -> SqliteMemoryStore:
    return request.app.state.memory_store


def get_http_log_store(request: Request) -> HttpLogStore:
    return request.app.state.http_log_store


def get_llm_service_store(request: Request) -> LLMServiceStore:
    return request.app.state.llm_service_store


def get_resolver(request: Request) -> "RoleResolver":
    return request.app.state.resolver


def get_multi_forwarder(request: Request) -> "MultiForwarder":
    return request.app.state.multi_forwarder


def get_vector_store(request: Request) -> "VectorStore":
    return request.app.state.vector_store


def get_reindex_progress(request: Request) -> "ReindexProgress":
    return request.app.state.reindex_progress


def get_conversation_store(request: Request) -> SqliteConversationStore:
    return request.app.state.conversation_store


def get_notification_store(request: Request) -> NotificationStore:
    return request.app.state.notification_store


def get_identity_store(request: Request) -> SqliteIdentityStore:
    return request.app.state.identity_store


def get_idempotency_store(request: Request) -> SqliteIdempotencyStore:
    return request.app.state.idempotency_store


def get_lorebook_store(request: Request) -> "SqliteLorebookStore":
    return request.app.state.lorebook_store


def get_space_policy_store(request: Request) -> "SqliteSpacePolicyStore":
    return request.app.state.space_policy_store
