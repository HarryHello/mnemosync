"""面板反向代理 query string 透传回归测试 (v0.4.1-beta.7).

回归: proxy_request 拼 URL 时丢弃 query string, 分离模式下 service_id /
分页 / 筛选等查询参数全部失效 (服务商模型列表永远返回全部).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import AsyncIterator

import pytest
import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from src.panel import proxy as proxy_mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def backend_port(monkeypatch: pytest.MonkeyPatch) -> int:
    """起一个 echo 后端 (返回收到的 path + query), 并把代理指向它."""
    stub = FastAPI()

    @stub.api_route("/{p:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def echo(p: str, request: Request) -> dict:
        return {"path": p, "query": request.url.query}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(stub, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "stub backend failed to start"

    monkeypatch.setattr(proxy_mod, "BACKEND_BASE", f"http://127.0.0.1:{port}")

    yield port
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def panel_app() -> FastAPI:
    """与 build_panel_app 相同形态的代理挂载 (不挂 auth, 聚焦代理本身)."""
    app = FastAPI()
    router = APIRouter()

    @router.api_route(
        "/panel/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy_panel(path: str, request: Request):
        return await proxy_mod.proxy_request(request, f"panel/{path}")

    app.include_router(router)
    return app


def test_proxy_forwards_query_string(backend_port: int, panel_app: FastAPI) -> None:
    client = TestClient(panel_app)
    resp = client.get("/panel/admin/models?service_id=BigModel&page=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "service_id=BigModel&page=2"
    assert body["path"] == "panel/admin/models"


def test_proxy_without_query(backend_port: int, panel_app: FastAPI) -> None:
    client = TestClient(panel_app)
    resp = client.get("/panel/admin/models")
    assert resp.status_code == 200, resp.text
    assert resp.json()["query"] == ""


def test_proxy_forwards_post_body(backend_port: int, panel_app: FastAPI) -> None:
    client = TestClient(panel_app)
    resp = client.post("/panel/admin/models", json={"service_id": "s1", "model": "m1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == "panel/admin/models"


async def _noop_async_iter() -> AsyncIterator[bytes]:
    yield b""
