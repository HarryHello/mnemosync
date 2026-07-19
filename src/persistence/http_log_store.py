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
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class HttpLogStore:
    """HTTP 日志的异步批量写盘 store."""

    _BATCH_SIZE = 50
    _FLUSH_INTERVAL_S = 0.5

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._writer_task: asyncio.Task | None = None
        self._stopping = False

    async def connect(self) -> None:
        if self._db is not None:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._init_schema(self._db)
        await self._db.commit()

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
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._writer_task.cancel()
            self._writer_task = None
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._queue = None

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

    async def _writer_loop(self) -> None:
        assert self._queue is not None
        assert self._db is not None
        batch: list[dict[str, Any]] = []
        while True:
            try:
                # 至少等一条
                first = await asyncio.wait_for(
                    self._queue.get(), timeout=self._FLUSH_INTERVAL_S
                )
                if first.get("__sentinel__"):
                    await self._flush(batch)
                    return
                batch.append(first)
                # 尽量凑齐一批
                while len(batch) < self._BATCH_SIZE:
                    try:
                        item = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item.get("__sentinel__"):
                        await self._flush(batch)
                        return
                    batch.append(item)
                await self._flush(batch)
                batch.clear()
            except asyncio.TimeoutError:
                if self._stopping:
                    return
                # 没新数据, 空转
                continue
            except asyncio.CancelledError:
                await self._flush(batch)
                raise
            except Exception as e:
                logger.exception("http_log writer_loop error: %s", e)
                batch.clear()
                await asyncio.sleep(0.5)

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        if not batch or self._db is None:
            return
        try:
            await self._db.executemany(
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
            await self._db.commit()
        except Exception as e:
            logger.warning("Failed to flush %d http_logs: %s", len(batch), e)

    # ============ 读取接口 (供 admin 路由使用) ============

    async def count(
        self,
        method: str | None = None,
        path: str | None = None,
        status: int | None = None,
    ) -> int:
        assert self._db is not None
        conditions, params = self._build_filter(method, path, status)
        where = " AND ".join(conditions) if conditions else "1=1"
        async with self._db.execute(
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
    ) -> list[tuple]:
        assert self._db is not None
        conditions, params = self._build_filter(method, path, status)
        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size
        async with self._db.execute(
            f"SELECT * FROM http_logs WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ) as cur:
            return await cur.fetchall()

    async def get_by_id(self, log_id: int) -> tuple | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM http_logs WHERE id = ?", (log_id,)
        ) as cur:
            return await cur.fetchone()

    async def clear_all(self) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM http_logs")
        await self._db.commit()

    async def cleanup(self, retention_days: int, max_records: int) -> None:
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM http_logs WHERE created_at < datetime('now', ? || ' days')",
            (-retention_days,),
        )
        await self._db.execute(
            "DELETE FROM http_logs WHERE id NOT IN ("
            "SELECT id FROM http_logs ORDER BY created_at DESC LIMIT ?)",
            (max_records,),
        )
        await self._db.commit()

    @staticmethod
    def _build_filter(
        method: str | None, path: str | None, status: int | None
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
        return conditions, params
