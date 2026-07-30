"""FastAPI 生命周期管理: 启动时打开所有长连接 Store, 关闭时清理."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from src.api.state import AppState
from src.core.config import get_settings
from src.core.constants import DEFAULT_PERSONA_ID
from src.core.identity.plugin_registry import discover_plugins
from src.core.memory.reindex import ReindexProgress
from src.core.models.resolver import RoleResolver
from src.core.persona.definition import PersonaDefinition
from src.core.tools.identity_binding import register_identity_binding_tools
from src.core.tools.internal_registry import InternalToolRegistry, set_internal_tool_registry
from src.infra.debug_bus import DebugEventBus
from src.infra.forwarder.debug_hook import set_debug_bus
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.space_lock import SpaceLockManager
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import (
    API_KEY_SOURCE_PANEL_DEBUG,
    SqliteApiKeyStore,
)
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

logger = logging.getLogger(__name__)


async def _conversation_prune_loop(
    store: SqliteConversationStore, window_days: int
) -> None:
    """每天清一次过期对话流水. 独立后台协程, 单进程单实例."""
    interval = 24 * 3600
    try:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        n = await store.delete_before(cutoff)
        if n > 0:
            logger.info(
                "启动清理: 删除 %d 条 conversation_turns (窗外, %d 天前)",
                n, window_days,
            )
    except Exception as e:
        logger.warning("启动清理 conversation_turns 失败: %s", e)

    while True:
        try:
            await asyncio.sleep(interval)
            cutoff = datetime.now(UTC) - timedelta(days=window_days)
            n = await store.delete_before(cutoff)
            if n > 0:
                logger.info(
                    "定时清理: 删除 %d 条 conversation_turns (窗外, %d 天前)",
                    n, window_days,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("conversation_turns 定时清理失败: %s", e)


async def _connect_stores(settings) -> dict:
    """创建并连接所有 Store, 返回已连接实例的字典.

    任一 Store 连接失败时, 已连接的会在后续 ``_close_all`` 中被正确关闭.
    """
    storage = settings.storage

    auth_store = SqliteAuthStore(str(storage.auth_db_abs))
    api_key_store = SqliteApiKeyStore(str(storage.api_key_db_abs))
    memory_store = SqliteMemoryStore(str(storage.memory_db_abs))
    http_log_store = HttpLogStore(str(storage.http_log_db_abs))
    llm_service_store = LLMServiceStore(str(storage.llm_db_abs))
    conversation_store = SqliteConversationStore(str(storage.conversation_db_abs))
    notification_store = NotificationStore(str(storage.notification_db_abs))
    identity_store = SqliteIdentityStore(str(storage.identity_db_abs))
    idempotency_store = SqliteIdempotencyStore(str(storage.idempotency_db_abs))
    persona_store = SqlitePersonaStore(str(storage.identity_db_abs))
    lorebook_store = SqliteLorebookStore(str(storage.identity_db_abs))
    space_policy_store = SqliteSpacePolicyStore(str(storage.identity_db_abs))

    instances = {
        "auth_store": auth_store,
        "api_key_store": api_key_store,
        "memory_store": memory_store,
        "http_log_store": http_log_store,
        "llm_service_store": llm_service_store,
        "conversation_store": conversation_store,
        "notification_store": notification_store,
        "identity_store": identity_store,
        "idempotency_store": idempotency_store,
        "persona_store": persona_store,
        "lorebook_store": lorebook_store,
        "space_policy_store": space_policy_store,
    }

    connect_order = [
        "auth_store", "api_key_store", "memory_store", "http_log_store",
        "conversation_store", "notification_store",
        "identity_store", "idempotency_store",
    ]
    for key in connect_order:
        await instances[key].connect()

    for key in ("llm_service_store", "persona_store", "lorebook_store", "space_policy_store"):
        await instances[key].init_db()

    return instances


async def _close_all(instances: dict) -> None:
    """关闭所有已初始化的 Store, 每个独立 try/except 保证互不影响."""
    close_order = [
        "conversation_store", "notification_store", "identity_store",
        "idempotency_store", "http_log_store", "memory_store",
        "api_key_store", "auth_store",
    ]
    for key in close_order:
        instance = instances.get(key)
        if instance is not None:
            try:
                await instance.close()
            except Exception as e:
                logger.warning("Error closing %s: %s", key, e)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """应用启动 / 关闭钩子.

    启动:
      * 打开 SQLite Store 的长连接 (WAL + NORMAL 同步).
      * 每个 store 在 connect() 内幂等地建表.
      * HttpLogStore 附带一个后台 asyncio.Task 消费写入队列.
      * 构建 AppState 并挂到 app.state.
    关闭:
      * 逆序 close, HttpLogStore flush 剩余日志.
    """
    settings = get_settings()

    # ── 1. 连接所有 Store ──────────────────────────────
    instances = {}
    try:
        instances = await _connect_stores(settings)
    except Exception:
        logger.error("Store 连接失败, 回滚已初始化的 Store")
        await _close_all(instances)
        raise

    # ── 2. 组装服务 ──────────────────────────────────
    identity_plugins = discover_plugins()
    logger.info("已加载 %d 个身份解析插件: %s", len(identity_plugins), list(identity_plugins.keys()))

    internal_tools = InternalToolRegistry()
    register_identity_binding_tools(internal_tools)
    set_internal_tool_registry(internal_tools)
    logger.info("已注册 %d 个内部 tool: %s", len(internal_tools.names), list(internal_tools.names))

    resolver = RoleResolver(instances["llm_service_store"])
    multi_forwarder = MultiForwarder(resolver)
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))
    reindex_progress = ReindexProgress()
    debug_bus = DebugEventBus(capacity=500, grace_seconds=30.0)

    async def _cleanup_debug_keys() -> None:
        n = await instances["api_key_store"].delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        if n > 0:
            logger.info("已清理 %d 个 panel-debug API Key", n)

    debug_bus.set_grace_callback(_cleanup_debug_keys)
    set_debug_bus(debug_bus)

    # 启动时清孤儿 panel-debug key
    try:
        orphan = await instances["api_key_store"].delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        if orphan > 0:
            logger.info("启动清理: 删除 %d 个孤儿 panel-debug API Key", orphan)
    except Exception as e:
        logger.warning("启动清理 panel-debug key 失败: %s", e)

    # ── 3. 构建 AppState 并挂载 ──────────────────────
    state = AppState(
        auth_store=instances["auth_store"],
        api_key_store=instances["api_key_store"],
        memory_store=instances["memory_store"],
        http_log_store=instances["http_log_store"],
        llm_service_store=instances["llm_service_store"],
        conversation_store=instances["conversation_store"],
        notification_store=instances["notification_store"],
        identity_store=instances["identity_store"],
        idempotency_store=instances["idempotency_store"],
        persona_store=instances["persona_store"],
        lorebook_store=instances["lorebook_store"],
        space_policy_store=instances["space_policy_store"],
        resolver=resolver,
        multi_forwarder=multi_forwarder,
        vector_store=vector_store,
        reindex_progress=reindex_progress,
        debug_bus=debug_bus,
        identity_plugins=identity_plugins,
        internal_tools=internal_tools,
        space_locks=SpaceLockManager(),
    )
    app.state = state

    # ── 4. 自动迁移: legacy 人格 ─────────────────────
    active = await instances["persona_store"].get_active()
    if active is None:
        legacy = PersonaDefinition.from_legacy(
            name=settings.persona.name,
            prompt=settings.persona.prompt,
            persona_addressing=settings.persona.relation.persona_addressing,
        )
        legacy.version = "1.0.0"
        await instances["persona_store"].save(legacy, changelog="从 config.local.toml 自动迁移", author="system")
        logger.info("✅ 自动迁移 legacy 人格到 DB: %s (version=%s)", legacy.name, legacy.version)

    # ── 5. 关系迁移: 修复 Actor 绑定前的孤立行 ──────
    try:
        actor_ids_with_groups = await instances["identity_store"].list_all_bound_actor_ids()
        if actor_ids_with_groups:
            fixed = 0
            for actor_id, group_id in actor_ids_with_groups:
                n = await instances["memory_store"].migrate_relationships_to_group(
                    DEFAULT_PERSONA_ID, actor_id, group_id,
                )
                fixed += n
            if fixed:
                logger.info(
                    "✅ 关系迁移: 修复 %d 条绑定前的孤立关系 (%d 个 Actor)",
                    fixed, len(actor_ids_with_groups),
                )
    except Exception as e:
        logger.warning("启动关系迁移失败 (可忽略): %s", e)

    # ── 6. 启动后台清理任务 ──────────────────────────
    prune_task = asyncio.create_task(
        _conversation_prune_loop(instances["conversation_store"], settings.storage.short_term_days),
        name="conversation-prune-loop",
    )
    state.conversation_prune_task = prune_task

    logger.info(
        "Stores connected; resolver + multi_forwarder + vector_store + reindex_progress + debug_bus ready"
    )

    try:
        yield
    finally:
        # ── 关闭序列 ──────────────────────────────────
        set_debug_bus(None)

        if prune_task is not None and not prune_task.done():
            prune_task.cancel()
            try:
                await prune_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            await multi_forwarder.close()
        except Exception as e:
            logger.warning("Error closing multi_forwarder: %s", e)

        try:
            await instances["api_key_store"].delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        except Exception as e:
            logger.warning("Error cleaning panel-debug keys on shutdown: %s", e)

        await _close_all(instances)
        logger.info("Stores closed")
