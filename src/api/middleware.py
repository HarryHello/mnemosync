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
from starlette.responses import Response, StreamingResponse

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

        try:
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
        except Exception as e:
            logger.error("Failed to initialize HTTP logs database: %s", e)

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
            logger.debug("Logged HTTP request: %s %s -> %s", kwargs["method"], kwargs["path"], kwargs["response_status"])
        except Exception as e:
            logger.warning("Failed to log HTTP request: %s", e)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求并记录日志."""
        # 跳过不需要记录的路径
        skip_paths = ("/health", "/docs", "/openapi.json", "/redoc")
        skip_extensions = (".js", ".css", ".ico", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ttf")

        if request.url.path in skip_paths:
            return await call_next(request)

        if request.url.path.endswith(skip_extensions):
            return await call_next(request)

        # 只记录 API 请求
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
                    # 尝试解析为 JSON
                    try:
                        request_body = json.loads(body.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        request_body = body.decode("utf-8", errors="replace")[:1000]  # 限制长度
            except Exception as e:
                logger.debug("Failed to read request body: %s", e)
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

        # 对于流式响应，包装一个 logging wrapper
        if isinstance(response, StreamingResponse):
            original_body_iterator = response.body_iterator

            async def logging_body_iterator():
                chunks = []
                async for chunk in original_body_iterator:
                    chunks.append(chunk)
                    yield chunk

                # 流结束后记录日志
                response_body = None
                try:
                    # 尝试解析最后的 JSON 响应
                    full_body = b"".join(chunks)
                    if full_body:
                        try:
                            response_body = json.loads(full_body.decode("utf-8", errors="replace"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            response_body = full_body.decode("utf-8", errors="replace")[:1000]
                except Exception:
                    pass

                # 同步写入数据库 (在流结束后)
                await self._log_request(
                    method=request.method,
                    path=request.url.path,
                    query_params=str(request.query_params) if request.query_params else None,
                    request_headers=request_headers,
                    request_body=request_body,
                    response_status=response.status_code,
                    response_body=response_body,
                    duration_ms=duration_ms,
                    client_ip=request.client.host if request.client else None,
                )

            # 返回新的流式响应
            return StreamingResponse(
                logging_body_iterator(),
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        else:
            # 非流式响应，直接记录
            response_body = None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    body = response.body
                    if body:
                        response_body = json.loads(body.decode("utf-8", errors="replace"))
                except Exception as e:
                    logger.debug("Failed to read response body: %s", e)

            # 记录日志
            await self._log_request(
                method=request.method,
                path=request.url.path,
                query_params=str(request.query_params) if request.query_params else None,
                request_headers=request_headers,
                request_body=request_body,
                response_status=response.status_code,
                response_body=response_body,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else None,
            )

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
