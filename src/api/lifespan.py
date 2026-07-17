"""FastAPI 生命周期管理: 启动时打开所有长连接 Store, 关闭时清理."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.memory.reindex import ReindexProgress
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.store import LLMServiceStore
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)


# 与旧代码保持一致的默认路径 (相对项目根). CLI 已把 cwd 切到项目根.
AUTH_DB_PATH = "data/auth.db"
API_KEY_DB_PATH = "data/api_keys.db"
HTTP_LOG_DB_PATH = "data/http_logs.db"
LLM_SERVICE_DB_PATH = "data/llm_service.db"


def _memory_db_path() -> str:
    from src.core.config import get_settings

    return str(get_settings().storage.memory_db_abs)


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

    await auth_store.connect()
    await api_key_store.connect()
    await memory_store.connect()
    await http_log_store.connect()
    await llm_service_store.init_db()

    resolver = RoleResolver(llm_service_store)
    multi_forwarder = MultiForwarder(resolver)

    # v0.2.4: 向量库 + reindex 进度单例. 向量库路径来自 settings.
    from src.core.config import get_settings as _get_settings
    vs_settings = _get_settings()
    vector_store = VectorStore(
        str(vs_settings.storage.chroma_dir_abs)
    )
    reindex_progress = ReindexProgress()

    app.state.auth_store = auth_store
    app.state.api_key_store = api_key_store
    app.state.memory_store = memory_store
    app.state.http_log_store = http_log_store
    app.state.llm_service_store = llm_service_store
    app.state.resolver = resolver
    app.state.multi_forwarder = multi_forwarder
    app.state.vector_store = vector_store
    app.state.reindex_progress = reindex_progress
    logger.info(
        "Stores connected (auth / api_key / memory / http_log / llm_service); "
        "resolver + multi_forwarder + vector_store + reindex_progress ready"
    )

    try:
        yield
    finally:
        try:
            await multi_forwarder.close()
        except Exception as e:
            logger.warning("Error closing multi_forwarder: %s", e)
        for store in (http_log_store, memory_store, api_key_store, auth_store):
            try:
                await store.close()
            except Exception as e:
                logger.warning("Error closing store %s: %s", type(store).__name__, e)
        logger.info("Stores closed")
