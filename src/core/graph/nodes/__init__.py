"""LangGraph node implementations.

Each node is a function: receives state, returns partial state update.
All LLM calls go through ``MultiForwarder`` + ``RoleResolver``, role → model
determined by ``role_bindings`` table, no hardcoded models in nodes.

v0.3.2 improvement: shared stores passed via LangGraph ``config["configurable"]``,
avoiding new SQLite connections per node execution. CLI etc. without config falls
back to lazy-loading (temporary stores from settings), maintaining backward
compatibility.

This package splits the original monolithic ``nodes.py`` into focused modules
while preserving the public API via re-exports.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.core.config import get_settings
from src.core.graph.state import AgentState
from src.core.memory import MemoryLifecycle  # noqa: F401 – re-exported for backward compat
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.persistence.relationship_store import SqliteRelationshipStore

from ._helpers import StoresDict  # noqa: F401 – re-exported for backward compat

logger = logging.getLogger(__name__)


# ── Infrastructure (must live here for test patching compatibility) ──


def _make_multi_forwarder_with_resolver() -> tuple[MultiForwarder, RoleResolver]:
    """Build MultiForwarder + resolver pair (sharing the same store)."""
    store = LLMServiceStore(str(get_settings().storage.llm_db_abs))
    resolver = RoleResolver(store)
    return MultiForwarder(resolver), resolver


def _make_multi_forwarder() -> MultiForwarder:
    """Build a standalone MultiForwarder (created once per node execution).

    Each call creates new store/resolver instances, reading latest bindings
    from role_bindings table, sharing the same SQLite file with external
    CLI/server calls.
    """
    fwd, _ = _make_multi_forwarder_with_resolver()
    return fwd


def _get_stores(config: RunnableConfig | None) -> StoresDict:
    """Extract shared store instances from LangGraph config.

    Keys that may be present in config["configurable"]:
      - multi_forwarder: MultiForwarder
      - resolver: RoleResolver
      - memory_store: SqliteMemoryStore
      - vector_store: VectorStore
      - notification_store: NotificationStore

    In server mode these are injected by lifespan via config, sharing long-lived
    connections; in CLI/test mode when not provided, falls back to lazy-loading
    (temporary short-lived connections, closed after use).
    """
    configurable = (config or {}).get("configurable", {}) if config else {}
    stores: StoresDict = dict(configurable)  # type: ignore[assignment]

    if "multi_forwarder" not in stores:
        stores["multi_forwarder"] = _make_multi_forwarder()
        stores["_owns_forwarder"] = True
    if "resolver" not in stores:
        _, stores["resolver"] = _make_multi_forwarder_with_resolver()
    if "memory_store" not in stores:
        s = get_settings()
        stores["memory_store"] = SqliteMemoryStore(str(s.storage.memory_db_abs))
    if "relationship_store" not in stores:
        s = get_settings()
        stores["relationship_store"] = SqliteRelationshipStore(str(s.storage.memory_db_abs))
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
            # Test environment may mock settings without notification_db_abs;
            # nodes not using notifications are unaffected
            stores["notification_store"] = None

    return stores


# ── Re-export all node implementations ───────────────────────────

# ── Re-export helper utilities ───────────────────────────────────
from ._helpers import (  # noqa: F401, E402 – re-exported for backward compat & test patching
    _compute_emotion,
    _format_emotion_text,
    _resolve_addressing,
    _retrieval_context,
)
from ._main_dialogue import (  # noqa: F401, E402 – re-exported for backward compat
    _extract_metadata,
    _invoke_llm,
    _prepare_context,
    _process_response,
    main_dialogue_node,
)
from ._memory_analysis import memory_analysis_node  # noqa: E402
from ._parse_request import parse_request_node  # noqa: E402
from ._proxy_thinking import proxy_thinking_node  # noqa: E402
from ._relationship_analysis import relationship_analysis_node  # noqa: E402

__all__ = [
    # Node implementations
    "parse_request_node",
    "proxy_thinking_node",
    "main_dialogue_node",
    "memory_analysis_node",
    "relationship_analysis_node",
    # Infrastructure
    "_get_stores",
    "_make_multi_forwarder",
    "_make_multi_forwarder_with_resolver",
    "StoresDict",
    "AgentState",
    # Helpers
    "_compute_emotion",
    "_format_emotion_text",
    "_resolve_addressing",
    "_retrieval_context",
    # Pipeline helpers (used by tests / external code)
    "_prepare_context",
    "_invoke_llm",
    "_process_response",
    "_extract_metadata",
]
