"""Lazy accessor functions for shared singletons.

Separated from ``__init__.py`` to break circular imports between the package
init and its sub-modules (``identity``, ``dispatch``, ``stream``, etc.).

Sub-modules should ``from ._accessors import ...`` rather than importing from
the package ``__init__``.
"""

from typing import Any

from fastapi import Request

from src.core.graph import build_graph
from src.infra.forwarder.multi import MultiForwarder
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.idempotency_store import SqliteIdempotencyStore
from src.persistence.identity_store import SqliteIdentityStore

# 全局缓存
_api_key_store: SqliteApiKeyStore | None = None
_compiled_graph = None


def _get_api_key_store() -> SqliteApiKeyStore:
    global _api_key_store
    if _api_key_store is None:
        _api_key_store = SqliteApiKeyStore("data/api_keys.db")
    return _api_key_store


def _get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def _get_multi_forwarder(http_request: Request) -> MultiForwarder:
    """从 AppState 取共享 MultiForwarder (由 lifespan 建立)."""
    from src.api.deps import _state
    return _state(http_request).multi_forwarder


def _get_conversation_store(http_request: Request) -> SqliteConversationStore:
    """从 AppState 取共享 SqliteConversationStore (由 lifespan 建立)."""
    from src.api.deps import _state
    return _state(http_request).conversation_store


def _get_identity_store(http_request: Request) -> SqliteIdentityStore | None:
    """从 AppState 取共享 SqliteIdentityStore (由 lifespan 建立)."""
    from src.api.deps import _state
    return _state(http_request).identity_store


def _get_plugins(http_request: Request) -> dict[str, Any]:
    """从 AppState 取已加载的插件注册表."""
    from src.api.deps import _state
    plugins = _state(http_request).identity_plugins
    return dict(plugins) if plugins else {}


def _get_idempotency_store(http_request: Request) -> SqliteIdempotencyStore | None:
    """从 AppState 取共享 SqliteIdempotencyStore (由 lifespan 建立)."""
    from src.api.deps import _state
    return _state(http_request).idempotency_store


def _get_debug_bus(http_request: Request):
    """从 AppState 取 DebugEventBus (可能为 None)."""
    from src.api.deps import _state
    return _state(http_request).debug_bus


def _get_persona_store(http_request: Request):
    """从 AppState 取 SqlitePersonaStore (可能为 None)."""
    from src.api.deps import _state
    return _state(http_request).persona_store


def _build_graph_config(http_request: Request) -> dict[str, Any]:
    """构建 LangGraph config["configurable"], 注入共享 store 单例.

    节点通过 ``_get_stores(config)`` 从 config 中取出长连接 store,
    避免每次节点执行新建 SQLite 连接. 测试环境下缺失的属性自动跳过
    (节点回退到懒加载).
    """
    from src.api.deps import _state
    state = _state(http_request)
    configurable: dict[str, Any] = {}
    for key in ("multi_forwarder", "resolver", "memory_store",
                "vector_store", "notification_store", "debug_bus",
                "identity_store", "persona_store", "lorebook_store",
                "space_policy_store", "agent_run_store"):
        val = getattr(state, key, None)
        if val is not None:
            configurable[key] = val
    return {"configurable": configurable}
