"""管理 API 路由.

提供日志查询、记忆管理、关系状态、Agent 提示词覆盖、仪表盘聚合接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import (
    get_api_key_store,
    get_http_log_store,
    get_memory_store,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PromptDetail,
    PromptHistoryItem,
    PromptHistoryResponse,
    PromptSummary,
    PromptValidateResponse,
    PromptWriteBody,
)
from src.core.prompts import get_prompt_store
from src.core.prompts.registry import PROMPT_REGISTRY
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
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
    query_params: Optional[str]
    request_headers: Optional[dict]
    request_body: Optional[dict | str | list]
    response_status: Optional[int]
    response_body: Optional[dict | str | list]
    duration_ms: Optional[float]
    client_ip: Optional[str]
    created_at: str


class HttpLogListResponse(BaseModel):
    items: list[HttpLogResponse]
    total: int
    page: int
    page_size: int


class MemoryResponse(BaseModel):
    id: str
    content: str
    memory_type: str
    importance: float
    decay_rate: float
    access_count: int
    source_user: str
    created_at: str
    last_accessed_at: Optional[str]


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int


class RelationshipResponse(BaseModel):
    persona_id: str
    user_id: str
    intimacy: float
    trust: float
    relationship_type: Optional[str]
    notes: Optional[str]
    updated_at: str


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

def _row_to_log(row: tuple) -> HttpLogResponse:
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


def _memory_to_response(m) -> MemoryResponse:
    return MemoryResponse(
        id=m.id,
        content=m.content,
        memory_type=m.memory_type.value,
        importance=m.importance,
        decay_rate=m.decay_rate,
        access_count=m.access_count,
        source_user=m.source_user or "",
        created_at=m.created_at.isoformat() if m.created_at else "",
        last_accessed_at=m.last_accessed.isoformat() if m.last_accessed else None,
    )


def _build_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.2.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ============================================================================
# Health Check
# ============================================================================

@router.get("/health", response_model=HealthResponse)
async def health_check():
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
    method: Optional[str] = None,
    path: Optional[str] = None,
    status: Optional[int] = None,
    store: HttpLogStore = Depends(get_http_log_store),
):
    """查询 HTTP 日志."""
    total = await store.count(method=method, path=path, status=status)
    rows = await store.list_paginated(
        page=page, page_size=page_size, method=method, path=path, status=status
    )
    items = [_row_to_log(r) for r in rows]
    return HttpLogListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/logs/{log_id}", response_model=HttpLogResponse)
async def get_log(
    log_id: int, store: HttpLogStore = Depends(get_http_log_store)
):
    """获取单条日志详情."""
    row = await store.get_by_id(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Log not found")
    return _row_to_log(row)


@router.delete("/logs")
async def clear_logs(store: HttpLogStore = Depends(get_http_log_store)):
    """清空所有日志."""
    await store.clear_all()
    return {"success": True, "message": "All logs cleared"}


# ============================================================================
# Memories
# ============================================================================

@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    source_user: str = "default",
    limit: int = Query(100, ge=1, le=500),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """查询记忆列表."""
    all_memories = await store.list_all_for_user(source_user, limit=limit)
    return MemoryListResponse(
        items=[_memory_to_response(m) for m in all_memories],
        total=len(all_memories),
    )


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """获取单条记忆详情."""
    memory = await store.get_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(memory)


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """删除记忆."""
    success = await store.delete(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "message": "Memory deleted"}


# ============================================================================
# Relationship
# ============================================================================

@router.get("/relationship", response_model=RelationshipResponse)
async def get_relationship(
    user_id: str = "default",
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """获取关系状态."""
    rel = await store.get_relationship("default", user_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")

    return RelationshipResponse(
        persona_id=rel.persona_id,
        user_id=rel.user_id,
        intimacy=rel.intimacy_score,
        trust=rel.trust_level,
        relationship_type=rel.type,
        notes=rel.notes,
        updated_at=rel.last_active.isoformat() if rel.last_active else "",
    )


# ============================================================================
# Agent Prompts (覆盖管理)
# ============================================================================

def _resolve_prompt_name(name: str) -> None:
    """路径穿越防御第一道: 只放行 registry 白名单.

    未命中直接 404, 不透露文件系统信息.
    """
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail="unknown prompt")


@router.get("/prompts", response_model=list[PromptSummary])
async def list_prompts():
    """列出所有 Agent 提示词 + 覆盖状态."""
    store = get_prompt_store()
    return [
        PromptSummary(
            name=info.name,
            description=info.description,
            placeholders=list(info.placeholders),
            overridden=info.overridden,
            version=info.version,
        )
        for info in store.list()
    ]


@router.get("/prompts/{name}", response_model=PromptDetail)
async def get_prompt(name: str):
    """获取单个提示词详情 (current + default 原文)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    info = store.get_info(name)
    current = store.load_raw(name, default=False)
    default = store.load_raw(name, default=True)
    return PromptDetail(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
        current=current,
        default=default,
    )


@router.put("/prompts/{name}", response_model=PromptSummary)
async def update_prompt(name: str, body: PromptWriteBody):
    """写入覆盖版本. 校验失败返回 400."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    result = store.validate(name, body.content)
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.error,
                "missing_placeholders": result.missing_placeholders,
            },
        )
    store.save(name, body.content)
    info = store.get_info(name)
    return PromptSummary(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
    )


@router.delete("/prompts/{name}", response_model=PromptSummary)
async def reset_prompt(name: str):
    """删除覆盖, 回到默认 (自动备份最后一版)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    store.reset(name)
    info = store.get_info(name)
    return PromptSummary(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
    )


@router.post("/prompts/{name}:validate", response_model=PromptValidateResponse)
async def validate_prompt(name: str, body: PromptWriteBody):
    """dry-run 校验 (不写盘)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    result = store.validate(name, body.content)
    return PromptValidateResponse(
        ok=result.ok,
        missing_placeholders=result.missing_placeholders,
        error=result.error,
    )


@router.get("/prompts/{name}/history", response_model=PromptHistoryResponse)
async def list_prompt_history(name: str):
    """列出该 name 在 .history/ 下的备份."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    items = [
        PromptHistoryItem(
            filename=item["filename"],
            mtime=item["mtime"],
            size=item["size"],
        )
        for item in store.list_history(name)
    ]
    return PromptHistoryResponse(items=items)
