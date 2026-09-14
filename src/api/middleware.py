"""HTTP 请求日志中间件.

设计:
  * 从 `request.app.state.http_log_store` (由 lifespan 提供) 取共享的异步 store.
  * 记录路径**不写盘**: 只 `enqueue()` 一条 dict, 由后台 worker 批量 flush.
  * 中间件从此不再阻塞 asyncio 事件循环, 仪表盘、流式接口等对延迟敏感的路径不再受拖累.
  * 若 app.state.debug_bus 存在且有活跃 SSE 订阅者, 顺带 emit inbound_request /
    inbound_response 事件供调试面板可视化。
  * 使用原生 ASGI 中间件, SSE/流式响应直接透传不缓冲.
"""

import json
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, cast

from starlette.middleware.base import BaseHTTPMiddleware, _StreamingResponse
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.constants import LOG_BODY_MAX_CHARS
from src.infra.debug_context import new_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)

# 默认日志保留配置 (与 HttpLogStore.cleanup() 参数一致, 供管理面板使用)
DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_RECORDS = 10000

# SSE 请求路径前缀 (这些路径返回流式响应, 不能被 BaseHTTPMiddleware 缓冲)
_SSE_PATHS = ("/panel/admin/debug/events/stream", "/v1/chat/completions")


def _log_debug(
    method: str, direction: str, url: str,
    headers: dict[str, Any] | None = None,
    body: Any = None,
    status: int | None = None,
) -> None:
    """通过标准 logger 输出调试请求/响应信息."""
    extra: dict[str, object] = {
        "direction": direction,
        "method": method,
        "url": str(url),
    }
    if status is not None:
        extra["status"] = status
    if headers:
        extra["headers"] = _truncate_json(headers, 500)
    if body:
        body_str = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
        extra["body"] = body_str[:LOG_BODY_MAX_CHARS]
    logger.debug("http %s %s %s", direction, method, url, extra=extra)


def _truncate_json(obj: object, max_len: int) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s[:max_len]


class HttpLogMiddleware(BaseHTTPMiddleware):
    """HTTP 请求日志中间件 (纯 async, 非阻塞入队)."""

    def __init__(self, app: ASGIApp, debug: bool = False):
        super().__init__(app)
        self.debug = debug or os.getenv("MNEMOSYNC_DEBUG") == "1"
        self._inner_app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 入口: SSE/流式请求走原生 ASGI 捕获, 其他走 BaseHTTPMiddleware."""
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "")

            # SSE 请求: 检查 Accept header 或路径, 不能走 BaseHTTPMiddleware
            # (它会缓冲 StreamingResponse 导致流式聊天卡死, v0.3.x 修复)
            is_sse = False
            if path in _SSE_PATHS:
                is_sse = True
            else:
                for key, value in scope.get("headers", []):
                    if key == b"accept" and b"text/event-stream" in value:
                        is_sse = True
                        break

            if is_sse:
                if path == "/v1/chat/completions":
                    # 流式聊天: 原生 ASGI 捕获请求体 (replay) + 响应状态/body
                    # (限长), 流结束后补一条完整日志 — 占位的空 body 行没有
                    # 调试价值 (beta.12 用户反馈: 下游 bot 的请求体看不到)
                    await self._log_and_passthrough(scope, receive, send, path, method)
                    return
                # 调试事件长连接: 只记占位, 不缓冲不等待结束
                store = self._store_from_scope(scope)
                if store is not None:
                    headers_dict = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
                    try:
                        store.enqueue({
                            "method": method,
                            "path": path,
                            "query_params": scope.get("query_string", b"").decode() or None,
                            "request_headers": headers_dict,
                            "request_body": None,
                            "response_status": None,  # 流式响应无法预知状态
                            "response_body": None,
                            "duration_ms": 0,
                            "client_ip": scope.get("client", ("",))[0] if scope.get("client") else None,
                        })
                    except Exception:
                        pass
                await self._inner_app(scope, receive, send)
                return

        # 非 SSE 请求: 走正常的 BaseHTTPMiddleware 路径
        await super().__call__(scope, receive, send)

    def _store_from_scope(self, scope: Scope) -> Any | None:
        try:
            app_state = scope.get("app", {})
            if hasattr(app_state, "state"):
                return getattr(app_state.state, "http_log_store", None)
        except Exception:
            pass
        return None

    async def _log_and_passthrough(
        self, scope: Scope, receive: Receive, send: Send, path: str, method: str,
    ) -> None:
        """流式聊天的原生 ASGI 日志捕获: 窥探请求体 + 包装 send 记录响应.

        请求体: 预读进缓冲并 replay 给应用 (上限 1MB, 防异常大历史爆内存);
        响应: 捕获状态码与 body 块 (累计上限 LOG_BODY_MAX_CHARS);
        correlation_id: 与非流式路径一致地设置, 调试事件不再全部挂 no-cid.
        """
        start_time = time.time()

        # 流式路径也要有 correlation_id (beta.12: 调试事件全挂 no-cid 的原因)
        if path.startswith("/v1/"):
            set_correlation_id(new_correlation_id())

        # ── 窥探请求体: 预读 + replay ─────────────────────────────────
        buffered: deque[bytes] = deque()
        buffered_size = 0
        peek_cap = 1024 * 1024
        pre_read_complete = False  # 预读是否见到流结束; 未见到时 replay 后还有残余
        original_receive = receive

        while True:
            msg = await original_receive()
            if msg["type"] != "http.request":
                break
            chunk = msg.get("body", b"")
            if buffered_size < peek_cap:
                buffered.append(chunk)
                buffered_size += len(chunk)
            if not msg.get("more_body", False) or buffered_size >= peek_cap:
                pre_read_complete = not msg.get("more_body", False)
                break
        # replay 会消费 buffered, 先留一份原始拷贝供日志使用
        peeked_body = b"".join(buffered)

        async def _receive_replay() -> Any:
            if buffered:
                chunk = buffered.popleft()
                more = bool(buffered) or not pre_read_complete
                return {"type": "http.request", "body": chunk, "more_body": more}
            return await original_receive()

        # ── 包装 send: 捕获状态与响应 body (限长) ─────────────────────
        status_holder: dict[str, int | None] = {"status": None}
        resp_chunks: list[bytes] = []
        resp_size = 0
        original_send = send

        async def _send_wrapper(message: Any) -> None:
            nonlocal resp_size
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status")
            elif message["type"] == "http.response.body":
                part = message.get("body", b"")
                if resp_size < LOG_BODY_MAX_CHARS:
                    resp_chunks.append(part)
                    resp_size += len(part)
            await original_send(message)

        try:
            await self._inner_app(scope, _receive_replay, _send_wrapper)
        finally:
            store = self._store_from_scope(scope)
            if store is not None:
                raw_body = peeked_body
                request_body: Any = None
                if raw_body:
                    try:
                        request_body = json.loads(raw_body.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        request_body = raw_body.decode("utf-8", errors="replace")[:LOG_BODY_MAX_CHARS]
                response_text = b"".join(resp_chunks).decode("utf-8", errors="replace")[:LOG_BODY_MAX_CHARS]
                headers_dict = {
                    k.decode(): v.decode() for k, v in scope.get("headers", [])
                }
                for key in list(headers_dict.keys()):
                    if "auth" in key.lower() or "key" in key.lower() or "token" in key.lower():
                        headers_dict[key] = "***"
                try:
                    store.enqueue({
                        "method": method,
                        "path": path,
                        "query_params": scope.get("query_string", b"").decode() or None,
                        "request_headers": headers_dict,
                        "request_body": request_body,
                        "response_status": status_holder["status"],
                        "response_body": response_text or None,
                        "duration_ms": round((time.time() - start_time) * 1000, 1),
                        "client_ip": scope.get("client", ("",))[0] if scope.get("client") else None,
                    })
                except Exception:
                    pass

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        skip_paths = ("/health", "/docs", "/openapi.json", "/redoc")
        skip_extensions = (".js", ".css", ".ico", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ttf")

        if request.url.path in skip_paths:
            return await call_next(request)

        if request.url.path.endswith(skip_extensions):
            return await call_next(request)

        # 只记录 API 请求 (面板 /panel/* 与 OpenAI 兼容层 /v1/*)
        if not request.url.path.startswith("/panel/") and not request.url.path.startswith("/v1/"):
            return await call_next(request)

        # 每个入站请求生成 correlation_id 挂到 contextvar, forwarder 出去打上游时会读
        cid = new_correlation_id()
        set_correlation_id(cid)

        # 调试面板只关心 OpenAI 兼容层 (/v1/*) — 面板自身的 /panel/* 请求 (拉调试事件
        # / 面板路由 / SSE) 不进调试事件流, 否则会把面板自己的轮询也算进去
        debug_bus = None
        if request.url.path.startswith("/v1/"):
            debug_bus = getattr(request.app.state, "debug_bus", None)

        start_time = time.time()

        # 读取请求体
        request_body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                if body:
                    try:
                        request_body = json.loads(body.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        request_body = body.decode("utf-8", errors="replace")[:LOG_BODY_MAX_CHARS]
            except Exception as e:
                logger.debug("Failed to read request body: %s", e)

        # 请求头脱敏
        request_headers = dict(request.headers)
        for key in list(request_headers.keys()):
            if "auth" in key.lower() or "key" in key.lower() or "token" in key.lower():
                request_headers[key] = "***"

        if self.debug:
            _log_debug(
                request.method,
                "REQUEST",
                str(request.url),
                headers=request_headers,
                body=request_body,
            )

        # Debug bus: 入站请求事件. 先解析请求头拿 API key note (若能)
        inbound_key_note: str | None = None
        if debug_bus and debug_bus.should_emit():
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                raw_key = auth[7:]
                api_key_store = getattr(request.app.state, "api_key_store", None)
                if api_key_store is not None:
                    try:
                        ak = await api_key_store.get_by_raw_key(raw_key)
                        if ak is not None:
                            inbound_key_note = ak.note
                    except Exception:
                        logger.debug("Failed to lookup API key for debug event", exc_info=True)
            debug_bus.emit(
                direction="inbound_request",
                correlation_id=cid,
                url=str(request.url),
                method=request.method,
                port=request.url.port,
                headers=request_headers,
                body=request_body,
                key_note=inbound_key_note,
            )

        response = await call_next(request)
        duration_ms = (time.time() - start_time) * 1000

        store = getattr(request.app.state, "http_log_store", None)

        # SSE 长连接 (非 _SSE_PATHS 但 content-type 为 text/event-stream): 不消费 body_iterator
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            if store is not None:
                store.enqueue({
                    "method": request.method,
                    "path": request.url.path,
                    "query_params": str(request.query_params) if request.query_params else None,
                    "request_headers": request_headers,
                    "request_body": request_body,
                    "response_status": response.status_code,
                    "response_body": None,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else None,
                })
            return response

        def _log(response_body: Any) -> None:
            if self.debug:
                _log_debug(
                    request.method,
                    "RESPONSE",
                    str(request.url),
                    status=response.status_code,
                    body=response_body,
                )
            if debug_bus and debug_bus.should_emit():
                debug_bus.emit(
                    direction="inbound_response",
                    correlation_id=cid,
                    url=str(request.url),
                    method=request.method,
                    port=request.url.port,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    key_note=inbound_key_note,
                    body=response_body,
                )
            if store is None:
                return
            store.enqueue({
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params) if request.query_params else None,
                "request_headers": request_headers,
                "request_body": request_body,
                "response_status": response.status_code,
                "response_body": response_body,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
            })

        # 收集响应体
        # Starlette 的 BaseHTTPMiddleware.call_next 返回 _StreamingResponse,
        # 其 body 不在 .body 属性中而在 .body_iterator 流中, 需要主动消费。
        # 同时兼容普通 StreamingResponse (如流式 chat).
        if isinstance(response, (_StreamingResponse, StreamingResponse)):
            body_chunks: list[bytes] = []
            async for chunk in response.body_iterator:
                body_chunks.append(cast(bytes, chunk))

            body_bytes = b"".join(body_chunks)
            response_body = None
            if body_bytes:
                try:
                    response_body = json.loads(body_bytes.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    response_body = body_bytes.decode("utf-8", errors="replace")[:LOG_BODY_MAX_CHARS]

            _log(response_body)

            # 返回新的 Response, 用收集到的 body_bytes 重建
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # 兜底: 普通 Response (如直接返回的 JSONResponse, 但 call_next 不会走这里)
        response_body = None
        if "json" in content_type:
            try:
                body = cast(bytes, response.body)
                if body:
                    response_body = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                logger.debug("Failed to parse response body for logging", exc_info=True)

        _log(response_body)
        return response
