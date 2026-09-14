"""流式聊天 (/v1/chat/completions) 日志捕获回归测试 (v0.4.1-beta.12).

回归: SSE 透传分支只插入占位日志 (request_body/response_body/response_status
全 None, duration 0), 下游 bot 的请求体在请求日志里完全看不到.
修复后: 原生 ASGI 窥探请求体 + 包装 send 捕获状态与响应 body (限长).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from src.api.middleware import HttpLogMiddleware
from src.persistence.http_log_store import HttpLogStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[HttpLogStore]:
    s = HttpLogStore(str(tmp_path / "logs.db"))
    await s.connect()
    yield s
    await s.close()


def _app(store: HttpLogStore) -> FastAPI:
    app = FastAPI()
    app.add_middleware(HttpLogMiddleware)

    @app.post("/v1/chat/completions")
    async def chat() -> StreamingResponse:
        async def gen():
            yield b'data: {"delta": "\\u4f60\\u597d"}\n\n'
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.state.http_log_store = store
    return app


_COLS = ("id", "method", "path", "query_params", "request_headers", "request_body",
         "response_status", "response_body", "duration_ms", "client_ip", "created_at")


async def _wait_row(store: HttpLogStore) -> dict | None:
    for _ in range(20):
        await store.flush_sync()
        rows = await store.list_paginated(page=1, page_size=5)
        if rows:
            return dict(zip(_COLS, rows[0], strict=True))
        import asyncio

        await asyncio.sleep(0.05)
    return None


async def test_streaming_chat_log_captures_bodies(store: HttpLogStore) -> None:
    """流式聊天的日志应含请求体 (JSON) / 响应状态 / SSE 响应文本 / 时长."""
    app = _app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "mnemosync-any", "messages": [{"role": "user", "content": "你好"}]},
        )
        assert resp.status_code == 200

    row = await _wait_row(store)
    assert row is not None, "流式聊天未产生日志行"
    assert row["path"] == "/v1/chat/completions"
    assert row["request_body"] is not None
    import json as _json

    req = _json.loads(row["request_body"]) if isinstance(row["request_body"], str) else row["request_body"]
    assert req["model"] == "mnemosync-any"
    assert req["messages"][0]["content"] == "你好"
    assert row["response_status"] == 200
    assert row["response_body"] is not None
    assert "data: " in row["response_body"]
    assert "[DONE]" in row["response_body"]
    assert (row["duration_ms"] or 0) >= 0


async def test_debug_stream_keeps_placeholder(store: HttpLogStore) -> None:
    """调试事件长连接保持占位行为 (不缓冲、不等待流结束)."""
    app = FastAPI()
    app.add_middleware(HttpLogMiddleware)

    @app.get("/panel/admin/debug/events/stream")
    async def stream() -> StreamingResponse:
        async def gen():
            yield b"data: hello\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.state.http_log_store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/panel/admin/debug/events/stream")
        assert resp.status_code == 200

    row = await _wait_row(store)
    assert row is not None
    assert row["request_body"] is None
    assert row["response_body"] is None
