"""管理 API 路由 - 核心 (健康检查、仪表盘、日志).

提供健康检查、仪表盘聚合、HTTP 日志查询接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import json
import logging
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import (
    get_api_key_store,
    get_http_log_store,
    get_memory_store,
)
from src.api.routes.auth import get_current_user
from src.core.prompts import get_prompt_store
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Schemas
# ============================================================================


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class HttpLogResponse(BaseModel):
    id: int
    method: str
    path: str
    query_params: str | None
    request_headers: dict[str, Any] | None
    request_body: dict[str, Any] | str | list[Any] | None
    response_status: int | None
    response_body: dict[str, Any] | str | list[Any] | None
    duration_ms: float | None
    client_ip: str | None
    created_at: str


class HttpLogListResponse(BaseModel):
    items: list[HttpLogResponse]
    total: int
    page: int
    page_size: int


class DashboardStatsResponse(BaseModel):
    """仪表盘聚合数据. 单端点替换 5 次往返."""

    api_keys: int
    memories: int
    logs: int
    prompts_total: int
    prompts_overridden: int
    health: HealthResponse


# ============================================================================
# Helpers
# ============================================================================


def _row_to_log(row: tuple[Any, ...]) -> HttpLogResponse:
    return HttpLogResponse(
        id=row[0],
        method=row[1],
        path=row[2],
        query_params=row[3],
        request_headers=json.loads(row[4]) if row[4] else None,
        request_body=json.loads(row[5]) if row[5] else None,
        response_status=row[6],
        response_body=json.loads(row[7]) if row[7] else None,
        duration_ms=row[8],
        client_ip=row[9],
        created_at=row[10],
    )


def _build_health() -> HealthResponse:
    try:
        pkg_version = _pkg_version("mnemosync")
    except PackageNotFoundError:
        pkg_version = "0.0.0+unknown"
    return HealthResponse(
        status="ok",
        version=pkg_version,
        timestamp=datetime.now(UTC).isoformat(),
    )


# ============================================================================
# Health Check
# ============================================================================


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查."""
    return _build_health()


# ============================================================================
# Dashboard aggregated stats (单端点)
# ============================================================================


@router.get("/stats", response_model=DashboardStatsResponse)
async def dashboard_stats(
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    api_key_store: SqliteApiKeyStore = Depends(get_api_key_store),
    http_log_store: HttpLogStore = Depends(get_http_log_store),
) -> DashboardStatsResponse:
    """仪表盘聚合: 一次查询代替 5 次 HTTP 往返."""
    prompt_store = get_prompt_store()
    prompt_infos = list(prompt_store.list())

    return DashboardStatsResponse(
        api_keys=await api_key_store.count_active(),
        memories=await memory_store.count_all(),
        logs=await http_log_store.count(),
        prompts_total=len(prompt_infos),
        prompts_overridden=sum(1 for p in prompt_infos if p.overridden),
        health=_build_health(),
    )


# ============================================================================
# HTTP Logs
# ============================================================================


@router.get("/logs", response_model=HttpLogListResponse)
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    since: str | None = Query(None, description="ISO 8601 时间, 只返回 >= 该时间的记录"),
    until: str | None = Query(None, description="ISO 8601 时间, 只返回 <= 该时间的记录"),
    store: HttpLogStore = Depends(get_http_log_store),
) -> HttpLogListResponse:
    """查询 HTTP 日志."""
    total = await store.count(method=method, path=path, status=status, since=since, until=until)
    rows = await store.list_paginated(
        page=page, page_size=page_size, method=method, path=path, status=status, since=since, until=until
    )
    items = [_row_to_log(r) for r in rows]
    return HttpLogListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/logs/{log_id}", response_model=HttpLogResponse)
async def get_log(
    log_id: int, store: HttpLogStore = Depends(get_http_log_store)
) -> HttpLogResponse:
    """获取单条日志详情."""
    row = await store.get_by_id(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Log not found")
    return _row_to_log(row)


@router.delete("/logs")
async def clear_logs(store: HttpLogStore = Depends(get_http_log_store)) -> dict[str, bool | str]:
    """清空所有日志."""
    await store.clear_all()
    return {"success": True, "message": "All logs cleared"}


@router.get("/check-update")
async def check_update() -> dict[str, Any]:
    """检查是否有新版本可用."""
    from src.infra.update_checker import check_for_update

    update = await check_for_update()
    if update:
        return {
            "update_available": True,
            "latest_version": update["latest_version"],
            "current_version": update["current_version"],
            "url": update["url"],
        }
    return {"update_available": False}
