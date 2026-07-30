"""应用状态容器: 统一管理所有长连接 Store / 服务的单例引用.

通过 ``AppState`` 数据类提供类型安全的属性访问, 消除 ``request.app.state.xxx``
字符串键访问带来的类型丢失与拼写错误.

使用方式::

    # lifespan.py 中构建并挂到 app.state
    state = AppState(...)
    app.state = state

    # deps.py 中通过 request.app.state 取
    def get_memory_store(request: Request) -> SqliteMemoryStore:
        return request.app.state.memory_store
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from src.core.identity.plugin import IdentityPlugin
    from src.core.memory.reindex import ReindexProgress
    from src.core.models.resolver import RoleResolver
    from src.core.tools.internal_registry import InternalToolRegistry
    from src.infra.debug_bus import DebugEventBus
    from src.infra.forwarder.multi import MultiForwarder
    from src.infra.llm_service.store import LLMServiceStore
    from src.infra.space_lock import SpaceLockManager
    from src.infra.vector_store import VectorStore
    from src.persistence.api_key_store import SqliteApiKeyStore
    from src.persistence.auth_store import SqliteAuthStore
    from src.persistence.conversation_store import SqliteConversationStore
    from src.persistence.http_log_store import HttpLogStore
    from src.persistence.idempotency_store import SqliteIdempotencyStore
    from src.persistence.identity_store import SqliteIdentityStore
    from src.persistence.lorebook_store import SqliteLorebookStore
    from src.persistence.memory_store import SqliteMemoryStore
    from src.persistence.notification_store import NotificationStore
    from src.persistence.persona_store import SqlitePersonaStore
    from src.persistence.space_policy_store import SqliteSpacePolicyStore


@dataclass
class AppState:
    """应用运行时状态容器.

    所有字段均为可选 (允许在启动期间逐步填充, 或在测试中按需注入).
    """

    # ── 持久化 Store ──────────────────────────────────
    auth_store: SqliteAuthStore | None = None
    api_key_store: SqliteApiKeyStore | None = None
    memory_store: SqliteMemoryStore | None = None
    http_log_store: HttpLogStore | None = None
    llm_service_store: LLMServiceStore | None = None
    conversation_store: SqliteConversationStore | None = None
    notification_store: NotificationStore | None = None
    identity_store: SqliteIdentityStore | None = None
    idempotency_store: SqliteIdempotencyStore | None = None
    persona_store: SqlitePersonaStore | None = None
    lorebook_store: SqliteLorebookStore | None = None
    space_policy_store: SqliteSpacePolicyStore | None = None

    # ── 服务 / 管理器 ──────────────────────────────────
    resolver: RoleResolver | None = None
    multi_forwarder: MultiForwarder | None = None
    vector_store: VectorStore | None = None
    reindex_progress: ReindexProgress | None = None
    debug_bus: DebugEventBus | None = None
    identity_plugins: dict[str, IdentityPlugin] | None = None
    internal_tools: InternalToolRegistry | None = None
    space_locks: SpaceLockManager | None = None
    conversation_prune_task: asyncio.Task[None] | None = None
