"""构建面板 FastAPI app.

面板进程 (16125) 是唯一入口:
  - 静态文件 (ui/dist)
  - /panel/auth/* 登录 (复用 auth_router)
  - /panel/admin/backend/* 后端管理 (自建)
  - 反向代理 /panel/admin/* (除 backend) / /v1/* → 后端进程
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import Response

from src.api.routes.auth import router as auth_router
from src.api.state import AppState
from src.core.config import get_settings
from src.persistence.auth_store import SqliteAuthStore

from .proxy import proxy_request
from .routes import router as backend_router

logger = logging.getLogger(__name__)

# 面板 API 前缀 (与后端一致, 前端相对路径不变)
_PANEL_PREFIX = "/panel"


@asynccontextmanager
async def _panel_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """面板生命周期: 只连接 auth_store (验证登录/管理后端)."""
    settings = get_settings()
    auth_store = SqliteAuthStore(str(settings.storage.auth_db_abs))
    await auth_store.connect()
    app.state = cast(Any, AppState(auth_store=auth_store))
    logger.info("面板进程就绪 (auth_store 已连接)")
    try:
        yield
    finally:
        await auth_store.close()


def build_panel_app() -> FastAPI:
    """构建面板 FastAPI app."""
    from fastapi.middleware.cors import CORSMiddleware

    from src.cli.cli import _mount_static, get_project_root

    try:
        from importlib.metadata import version as _get_version
        _panel_version = _get_version("mnemosync")
    except Exception:
        _panel_version = "0.0.0+unknown"

    app = FastAPI(
        title="Mnemosync Panel",
        description="Mnemosync 轻量管理面板 (前后端分离的前端入口)",
        version=_panel_version,
        lifespan=_panel_lifespan,
    )

    # CORS (开发模式)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 登录鉴权 + 后端管理 (先注册, 精确路由优先于代理路由)
    app.include_router(auth_router, prefix=_PANEL_PREFIX)
    app.include_router(backend_router, prefix=_PANEL_PREFIX)

    # 反向代理: /panel/* → 后端 (面板自身的 auth 和 backend 路由已先注册, 优先匹配)
    # 这里只处理面板没有注册的路径 (如 /panel/api-keys, /panel/admin/memories 等)
    @app.api_route(
        "/panel/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy_panel(path: str, request: Request) -> Response:
        return await proxy_request(request, f"panel/{path}")

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy_v1(path: str, request: Request) -> Response:
        return await proxy_request(request, f"v1/{path}")

    # 静态文件 + SPA 兜底
    _mount_static(app, get_project_root())

    return app
