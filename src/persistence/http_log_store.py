"""HTTP 请求日志存储 (aiosqlite + 后台批量写入).

设计:
  * 与 auth_store/memory_store/api_key_store 统一使用长连接 (WAL + NORMAL 同步).
  * `enqueue()` 只把 payload 塞进 asyncio.Queue, **不 await 写盘**,
    这样中间件的响应路径永不被 sqlite 阻塞.
  * 后台 worker `_writer_loop` 批量 flush, 兼顾吞吐与实时性.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiosqlite

from src.persistence.base import SqliteStore

logger = logging.getLogger(__name__)


class HttpLogStore(SqliteStore):
    """HTTP 日志的异步批量写盘 store."""

    _enable_foreign_keys = False

    _BATCH_SIZE = 50
    _FLUSH_INTERVAL_S = 0.5

    def __init__(self, db_path: str):
        super().__init__(db_path)
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._writer_task: asyncio.Task | None = None
        self._writer_conn: aiosqlite.Connection | None = None
        self._stopping = False

    async def connect(self) -> None:
        await super().connect()
        self._queue = asyncio.Queue(maxsize=10000)
        self._stopping = False
        self._writer_task = asyncio.create_task(self._writer_loop(), name="http-log-writer")

    async def close(self) -> None:
        self._stopping = True
        if self._writer_task is not None:
            # 唤醒 writer 让它 flush 剩余
            if self._queue is not None:
                try:
                    self._queue.put_nowait({"__sentinel__": True})
                except asyncio.QueueFull:
                    pass
            try:
                await asyncio.wait_for(self._writer_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._writer_task.cancel()
            self._writer_task = None
        # 关闭 writer 专用连接
        if self._writer_conn is not None:
            try:
                await self._writer_conn.close()
            except Exception:
                pass
            self._writer_conn = None
        self._queue = None
        await super().close()

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS http_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                query_params TEXT,
                request_headers TEXT,
                request_body TEXT,
                response_status INTEGER,
                response_body TEXT,
                duration_ms REAL,
                client_ip TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_logs_created_at ON http_logs(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_logs_path ON http_logs(path)"
        )

    def enqueue(self, entry: dict[str, Any]) -> None:
        """非阻塞入队. 队列满时静默丢弃 (记录不应影响请求)."""
        if self._queue is None or self._stopping:
            return
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("http_log queue full, dropping entry")

    async def flush_sync(self) -> None:
        """阻塞直到队列中已入队的所有条目都写盘.

        供测试与需要"写后读"一致性的场景使用. 在队尾入队一个 flush 标记,
        由 writer 处理完它 (FIFO 保证标记之前的所有条目都已落盘) 后唤醒.
        """
        if self._queue is None or self._writer_task is None:
            return
        event = asyncio.Event()
        await self._queue.put({"__flush_request__": event})
        await asyncio.wait_for(event.wait(), timeout=5.0)

    async def _get_writer_conn(self) -> aiosqlite.Connection:
        """Return a long-lived writer connection, creating it on first use."""
        if self._writer_conn is None:
            self._writer_conn = await aiosqlite.connect(self.db_path)
            await self._writer_conn.execute("PRAGMA journal_mode=WAL")
            await self._writer_conn.execute("PRAGMA synchronous=NORMAL")
        return self._writer_conn

    async def _writer_loop(self) -> None:
        batch: list[dict[str, Any]] = []
        while True:
            try:
                queue = self._queue
                if queue is None:
                    return
                # 至少等一条
                first = await asyncio.wait_for(
                    queue.get(), timeout=self._FLUSH_INTERVAL_S
                )
                if first.get("__sentinel__"):
                    await self._flush(batch)
                    return
                if first.get("__flush_request__"):
                    await self._flush(batch)
                    first["__flush_request__"].set()
                    continue
                batch.append(first)
                # 尽量凑齐一批
                while len(batch) < self._BATCH_SIZE:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item.get("__sentinel__"):
                        await self._flush(batch)
                        return
                    if item.get("__flush_request__"):
                        await self._flush(batch)
                        item["__flush_request__"].set()
                        batch.clear()
                        break
                    batch.append(item)
                await self._flush(batch)
                batch.clear()
            except TimeoutError:
                if self._stopping:
                    return
                # 没新数据, 空转
                continue
            except asyncio.CancelledError:
                await self._flush(batch)
                raise
            except Exception as e:
                logger.exception("http_log writer_loop error: %s", e)
                self._writer_conn = None  # 重建连接
                batch.clear()
                await asyncio.sleep(0.5)

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        db = await self._get_writer_conn()
        try:
            await db.executemany(
                """
                INSERT INTO http_logs
                (method, path, query_params, request_headers, request_body,
                 response_status, response_body, duration_ms, client_ip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e["method"],
                        e["path"],
                        e.get("query_params"),
                        json.dumps(e["request_headers"], ensure_ascii=False)
                        if e.get("request_headers") else None,
                        json.dumps(e["request_body"], ensure_ascii=False)
                        if e.get("request_body") is not None else None,
                        e.get("response_status"),
                        json.dumps(e["response_body"], ensure_ascii=False)
                        if e.get("response_body") is not None else None,
                        e.get("duration_ms"),
                        e.get("client_ip"),
                    )
                    for e in batch
                ],
            )
            await db.commit()
        except Exception as e:
            logger.warning("Failed to flush %d http_logs: %s", len(batch), e)

    # ============ 读取接口 (供 admin 路由使用) ============

    async def count(
        self,
        method: str | None = None,
        path: str | None = None,
        status: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        conditions, params = self._build_filter(method, path, status, since, until)
        where = " AND ".join(conditions) if conditions else "1=1"
        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM http_logs WHERE {where}", params
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def list_paginated(
        self,
        page: int,
        page_size: int,
        method: str | None = None,
        path: str | None = None,
        status: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[tuple]:
        conditions, params = self._build_filter(method, path, status, since, until)
        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size
        async with self._conn() as db:
            async with db.execute(
                f"SELECT * FROM http_logs WHERE {where} "
                f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ) as cur:
                return await cur.fetchall()

    async def get_by_id(self, log_id: int) -> tuple | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM http_logs WHERE id = ?", (log_id,)
            ) as cur:
                return await cur.fetchone()

    async def clear_all(self) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM http_logs")
            await db.commit()

    async def cleanup(self, retention_days: int, max_records: int) -> None:
        async with self._conn() as db:
            await db.execute(
                "DELETE FROM http_logs WHERE created_at < datetime('now', ? || ' days')",
                (-retention_days,),
            )
            await db.execute(
                "DELETE FROM http_logs WHERE id NOT IN ("
                "SELECT id FROM http_logs ORDER BY created_at DESC LIMIT ?)",
                (max_records,),
            )
            await db.commit()

    @staticmethod
    def _build_filter(
        method: str | None,
        path: str | None,
        status: int | None,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if method:
            conditions.append("method = ?")
            params.append(method.upper())
        if path:
            conditions.append("path LIKE ?")
            params.append(f"%{path}%")
        if status is not None:
            conditions.append("response_status = ?")
            params.append(status)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        if until:
            conditions.append("created_at <= ?")
            params.append(until)
        return conditions, params
