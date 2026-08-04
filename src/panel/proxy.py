"""反向代理: 把面板请求转发到后端进程.

后端进程提供 /panel/admin/* 与 /v1/* 业务 API. 面板进程作为唯一入口,
把这两类请求代理到后端 (默认 127.0.0.1:16126), 支持 SSE 流式转发.

后端未启动时返回 503, 前端可感知并提示.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

# 后端地址, 默认 127.0.0.1:16126 (仅本机, 不对外暴露)
BACKEND_BASE = os.getenv("MNEMOSYNC_BACKEND_URL", "http://127.0.0.1:16126")

# 透传时移除的请求头 (host 由 httpx 重设; content-length 由 httpx 计算)
_SKIP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}


async def proxy_request(request: Request, path: str) -> Response:
    """把请求转发到后端, 流式返回响应.

    Args:
        request: 原始请求
        path: 待转发路径 (不含前导 /, 如 "v1/chat/completions")
    """
    url = f"{BACKEND_BASE}/{path}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _SKIP_HEADERS
    }
    body = await request.body()

    try:
        # trust_env=False: 本地后端直连, 不走系统代理 (避免代理把 127.0.0.1 请求泄走)
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
            req = client.build_request(
                request.method, url, headers=headers, content=body,
            )
            resp = await client.send(req, stream=True)
    except httpx.TransportError:
        # 传输层错误 (ConnectError/ConnectTimeout/ReadTimeout) = 后端不可达
        logger.warning("后端未启动, 代理 %s 失败", path)
        return JSONResponse(
            status_code=503,
            content={"detail": "后端未启动, 请先在面板中启动后端服务"},
        )
    except httpx.HTTPError as e:
        logger.warning("代理 %s 失败: %s", path, e)
        return JSONResponse(status_code=502, content={"detail": f"后端代理失败: {e}"})

    # 流式转发响应体 (支持 SSE / 流式 chat)
    content_type = resp.headers.get("content-type", "")
    headers_out = {k: v for k, v in resp.headers.items() if k.lower() not in _SKIP_HEADERS}

    async def _iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(
        _iter(),
        status_code=resp.status_code,
        headers=headers_out,
        media_type=content_type or None,
    )
