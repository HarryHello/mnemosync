"""HTTP 请求日志中间件.

记录所有 API 请求/响应到数据库，用于调试和审计.
"""

import asyncio
import json
import time
import logging
from typing import Callable

import aiosqlite
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_RECORDS = 10000
DEFAULT_DB_PATH = "data/http_logs.db"


class HttpLogMiddleware(BaseHTTPMiddleware):
    """HTTP 请求日志中间件."""

    def __init__(self, app, db_path: str = DEFAULT_DB_PATH):
        super().__init__(app)
        self.db_path = db_path
        self._initialized = False

    async def _ensure_db(self):
        """确保数据库表存在."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
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
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_http_logs_created_at 
                ON http_logs(created_at)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_http_logs_path 
                ON http_logs(path)
            """)
            await db.commit()

        self._initialized = True

    async def _log_request(self, **kwargs):
        """写入日志到数据库."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT INTO http_logs 
                    (method, path, query_params, request_headers, request_body, 
                     response_status, response_body, duration_ms, client_ip)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    kwargs["method"],
                    kwargs["path"],
                    kwargs["query_params"],
                    json.dumps(kwargs["request_headers"], ensure_ascii=False) if kwargs["request_headers"] else None,
                    json.dumps(kwargs["request_body"], ensure_ascii=False) if kwargs["request_body"] else None,
                    kwargs["response_status"],
                    json.dumps(kwargs["response_body"], ensure_ascii=False) if kwargs["response_body"] else None,
                    kwargs["duration_ms"],
                    kwargs["client_ip"],
                ))
                await db.commit()
        except Exception as e:
            logger.warning("Failed to log HTTP request: %s", e)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求并记录日志."""
        # 跳过健康检查和静态文件
        skip_paths = ("/health", "/docs", "/openapi.json", "/redoc")
        skip_extensions = (".js", ".css", ".ico", ".png", ".jpg", ".svg")

        if request.url.path in skip_paths:
            return await call_next(request)

        if request.url.path.endswith(skip_extensions):
            return await call_next(request)

        # 跳过前端路由 (非 API 路径)
        if not request.url.path.startswith("/api/") and not request.url.path.startswith("/v1/"):
            return await call_next(request)

        await self._ensure_db()

        # 记录开始时间
        start_time = time.time()

        # 读取请求体
        request_body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                if body:
                    request_body = body.decode("utf-8", errors="replace")
                    # 尝试解析为 JSON
                    try:
                        request_body = json.loads(request_body)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                request_body = "<read error>"

        # 获取请求头
        request_headers = dict(request.headers)
        # 隐藏敏感头
        for key in list(request_headers.keys()):
            if "auth" in key.lower() or "key" in key.lower() or "token" in key.lower():
                request_headers[key] = "***"

        # 处理响应
        response = await call_next(request)

        # 计算耗时
        duration_ms = (time.time() - start_time) * 1000

        # 读取响应体 (只读取小型 JSON 响应)
        response_body = None
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                # 读取响应 body
                body_chunks = []
                async for chunk in response.body_iterator:
                    if isinstance(chunk, str):
                        body_chunks.append(chunk.encode())
                    else:
                        body_chunks.append(chunk)

                full_body = b"".join(body_chunks)
                if full_body:
                    response_body = json.loads(full_body.decode("utf-8", errors="replace"))

                # 重建响应 (因为已经消费了 body_iterator)
                response = Response(
                    content=full_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            except Exception as e:
                logger.debug("Failed to read response body: %s", e)

        # 异步写入数据库
        asyncio.create_task(self._log_request(
            method=request.method,
            path=request.url.path,
            query_params=str(request.query_params) if request.query_params else None,
            request_headers=request_headers,
            request_body=request_body,
            response_status=response.status_code,
            response_body=response_body,
            duration_ms=duration_ms,
            client_ip=request.client.host if request.client else None,
        ))

        return response


async def cleanup_old_logs(db_path: str = DEFAULT_DB_PATH, retention_days: int = DEFAULT_RETENTION_DAYS, max_records: int = DEFAULT_MAX_RECORDS):
    """清理过期日志."""
    try:
        async with aiosqlite.connect(db_path) as db:
            # 按时间清理
            await db.execute("""
                DELETE FROM http_logs 
                WHERE created_at < datetime('now', ? || ' days')
            """, (-retention_days,))

            # 按数量清理 (保留最新的)
            await db.execute("""
                DELETE FROM http_logs 
                WHERE id NOT IN (
                    SELECT id FROM http_logs 
                    ORDER BY created_at DESC 
                    LIMIT ?
                )
            """, (max_records,))

            await db.commit()
            logger.info("Cleaned up old HTTP logs")
    except Exception as e:
        logger.warning("Failed to cleanup HTTP logs: %s", e)
