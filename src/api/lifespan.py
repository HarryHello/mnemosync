"""FastAPI 生命周期管理: 启动时打开所有长连接 Store, 关闭时清理."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from src.core.memory.reindex import ReindexProgress
from src.core.models.resolver import RoleResolver
from src.infra.debug_bus import DebugEventBus
from src.infra.forwarder.debug_hook import set_debug_bus
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import (
    API_KEY_SOURCE_PANEL_DEBUG,
    SqliteApiKeyStore,
)
from src.persistence.auth_store import SqliteAuthStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)


# 与旧代码保持一致的默认路径 (相对项目根). CLI 已把 cwd 切到项目根.
AUTH_DB_PATH = "data/auth.db"
API_KEY_DB_PATH = "data/api_keys.db"
HTTP_LOG_DB_PATH = "data/http_logs.db"
LLM_SERVICE_DB_PATH = "data/llm_service.db"
NOTIFICATION_DB_PATH = "data/notifications.db"
IDENTITY_DB_PATH = "data/identity.db"


def _memory_db_path() -> str:
    from src.core.config import get_settings

    return str(get_settings().storage.memory_db_abs)


def _conversation_db_path() -> str:
    from src.core.config import get_settings

    return str(get_settings().storage.conversation_db_abs)


async def _conversation_prune_loop(
    store: SqliteConversationStore, window_days: int
) -> None:
    """每天清一次过期对话流水. 独立后台协程, 单进程单实例."""
    interval = 24 * 3600
    # 启动即清一次, 避免服务器长时间不重启导致老数据堆积
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
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
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
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
      * 打开 4 个 SQLite Store 的长连接 (WAL + NORMAL 同步).
      * 每个 store 在 connect() 内幂等地建表.
      * HttpLogStore 附带一个后台 asyncio.Task 消费写入队列.
    关闭:
      * 逆序 close, HttpLogStore flush 剩余日志.
    """
    auth_store = SqliteAuthStore(AUTH_DB_PATH)
    api_key_store = SqliteApiKeyStore(API_KEY_DB_PATH)
    memory_store = SqliteMemoryStore(_memory_db_path())
    http_log_store = HttpLogStore(HTTP_LOG_DB_PATH)
    llm_service_store = LLMServiceStore(LLM_SERVICE_DB_PATH)
    conversation_store = SqliteConversationStore(_conversation_db_path())
    notification_store = NotificationStore(NOTIFICATION_DB_PATH)
    identity_store = SqliteIdentityStore(IDENTITY_DB_PATH)

    await auth_store.connect()
    await api_key_store.connect()
    await memory_store.connect()
    await http_log_store.connect()
    await llm_service_store.init_db()
    await conversation_store.connect()
    await notification_store.connect()
    await identity_store.connect()

    resolver = RoleResolver(llm_service_store)
    multi_forwarder = MultiForwarder(resolver)

    # v0.2.4: 向量库 + reindex 进度单例. 向量库路径来自 settings.
    from src.core.config import get_settings as _get_settings
    vs_settings = _get_settings()
    vector_store = VectorStore(
        str(vs_settings.storage.chroma_dir_abs)
    )
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

    # 后台任务: 每 24h 清理窗外对话流水
    from src.core.config import get_settings as _gs
    _window_days = _gs().storage.short_term_days
    prune_task = asyncio.create_task(
        _conversation_prune_loop(conversation_store, _window_days),
        name="conversation-prune-loop",
    )
    app.state.conversation_prune_task = prune_task

    logger.info(
        "Stores connected (auth / api_key / memory / http_log / llm_service / conversation / identity); "
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
        for store in (conversation_store, notification_store, identity_store, http_log_store, memory_store, api_key_store, auth_store):
            try:
                await store.close()
            except Exception as e:
                logger.warning("Error closing store %s: %s", type(store).__name__, e)
        logger.info("Stores closed")
