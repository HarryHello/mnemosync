"""FastAPI 生命周期管理: 启动时打开所有长连接 Store, 关闭时清理."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from src.core.config import get_settings
from src.core.memory.reindex import ReindexProgress
from src.core.models.resolver import RoleResolver
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
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.lorebook_store import SqliteLorebookStore
from src.persistence.persona_store import SqlitePersonaStore
from src.persistence.space_policy_store import SqliteSpacePolicyStore

logger = logging.getLogger(__name__)


async def _conversation_prune_loop(
    store: SqliteConversationStore, window_days: int
) -> None:
    """每天清一次过期对话流水. 独立后台协程, 单进程单实例."""
    interval = 24 * 3600
    # 启动即清一次, 避免服务器长时间不重启导致老数据堆积
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


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """应用启动 / 关闭钩子.

    启动:
      * 打开 SQLite Store 的长连接 (WAL + NORMAL 同步).
      * 每个 store 在 connect() 内幂等地建表.
      * HttpLogStore 附带一个后台 asyncio.Task 消费写入队列.
    关闭:
      * 逆序 close, HttpLogStore flush 剩余日志.
    """
    settings = get_settings()
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
    persona_store = SqlitePersonaStore(str(storage.identity_db_abs))  # 复用 identity.db

    await auth_store.connect()
    await api_key_store.connect()
    await memory_store.connect()
    await http_log_store.connect()
    await llm_service_store.init_db()
    await conversation_store.connect()
    await notification_store.connect()
    await identity_store.connect()
    await idempotency_store.connect()
    await persona_store.init_db()
    lorebook_store = SqliteLorebookStore(str(storage.identity_db_abs))  # 复用 identity.db
    await lorebook_store.init_db()
    space_policy_store = SqliteSpacePolicyStore(str(storage.identity_db_abs))  # 复用 identity.db
    await space_policy_store.init_db()

    # 加载身份解析插件 (v0.3.1)
    from src.core.identity.plugin_registry import discover_plugins
    identity_plugins = discover_plugins()
    logger.info("已加载 %d 个身份解析插件: %s", len(identity_plugins), list(identity_plugins.keys()))

    # 内部 tool 注册表 (v0.3.3)
    from src.core.tools.internal_registry import InternalToolRegistry, set_internal_tool_registry
    from src.core.tools.identity_binding import register_identity_binding_tools
    internal_tools = InternalToolRegistry()
    register_identity_binding_tools(internal_tools)
    set_internal_tool_registry(internal_tools)
    logger.info("已注册 %d 个内部 tool: %s", len(internal_tools.names), list(internal_tools.names))

    resolver = RoleResolver(llm_service_store)
    multi_forwarder = MultiForwarder(resolver)

    # v0.2.4: 向量库 + reindex 进度单例. 向量库路径来自 settings.
    vector_store = VectorStore(str(storage.chroma_dir_abs))
    reindex_progress = ReindexProgress()

    # 调试面板事件总线. 订阅数掉到 0 且 grace 到期时清理 panel-debug key.
    debug_bus = DebugEventBus(capacity=500, grace_seconds=30.0)

    async def _cleanup_debug_keys() -> None:
        n = await api_key_store.delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        if n > 0:
            logger.info("已清理 %d 个 panel-debug API Key (调试面板断开 > 30s)", n)

    debug_bus.set_grace_callback(_cleanup_debug_keys)
    set_debug_bus(debug_bus)

    # 启动时也清一次孤儿 panel-debug key (上次进程崩溃残留)
    try:
        orphan = await api_key_store.delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        if orphan > 0:
            logger.info("启动清理: 删除 %d 个孤儿 panel-debug API Key", orphan)
    except Exception as e:
        logger.warning("启动清理 panel-debug key 失败: %s", e)

    app.state.auth_store = auth_store
    app.state.api_key_store = api_key_store
    app.state.memory_store = memory_store
    app.state.http_log_store = http_log_store
    app.state.llm_service_store = llm_service_store
    app.state.resolver = resolver
    app.state.multi_forwarder = multi_forwarder
    app.state.vector_store = vector_store
    app.state.reindex_progress = reindex_progress
    app.state.debug_bus = debug_bus
    app.state.conversation_store = conversation_store
    app.state.notification_store = notification_store
    app.state.identity_store = identity_store
    app.state.idempotency_store = idempotency_store
    app.state.identity_plugins = identity_plugins
    app.state.space_locks = SpaceLockManager()
    app.state.persona_store = persona_store
    app.state.lorebook_store = lorebook_store
    app.state.space_policy_store = space_policy_store

    # 自动迁移: 从 config.local.toml 的 legacy 人格创建首个 DB 版本
    from src.core.persona.definition import PersonaDefinition
    active = await persona_store.get_active()
    if active is None:
        legacy = PersonaDefinition.from_legacy(
            name=settings.persona.name,
            prompt=settings.persona.prompt,
            persona_addressing=settings.persona.relation.persona_addressing,
            user_addressing=settings.persona.relation.user_addressing,
            context=settings.persona.relation.context,
        )
        legacy.version = "1.0.0"
        await persona_store.save(legacy, changelog="从 config.local.toml 自动迁移", author="system")
        logger.info("✅ 自动迁移 legacy 人格到 DB: %s (version=%s)", legacy.name, legacy.version)

    # 后台任务: 每 24h 清理窗外对话流水
    prune_task = asyncio.create_task(
        _conversation_prune_loop(conversation_store, storage.short_term_days),
        name="conversation-prune-loop",
    )
    app.state.conversation_prune_task = prune_task

    logger.info(
        "Stores connected (auth / api_key / memory / http_log / llm_service / conversation / identity / persona); "
        "resolver + multi_forwarder + vector_store + reindex_progress + debug_bus ready"
    )

    try:
        yield
    finally:
        set_debug_bus(None)
        # 先停后台清理任务
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
        # 关闭时也清一次 panel-debug key
        try:
            await api_key_store.delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        except Exception as e:
            logger.warning("Error cleaning panel-debug keys on shutdown: %s", e)
        for store in (conversation_store, notification_store, identity_store, idempotency_store, http_log_store, memory_store, api_key_store, auth_store):
            try:
                await store.close()
            except Exception as e:
                logger.warning("Error closing store %s: %s", type(store).__name__, e)
        logger.info("Stores closed")
