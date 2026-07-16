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
    get_llm_service_store,
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
from src.infra.forwarder import Forwarder, ForwarderConfig, UpstreamError, UpstreamTimeout
from src.infra.llm_service.models import (
    LLMServiceProvider,
    ModelConfiguration,
    ModelType,
)
from src.infra.llm_service.store import LLMServiceStore
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


class UpstreamServiceResponse(BaseModel):
    id: str
    base_url: str
    api_key_masked: str
    created_at: str
    updated_at: str
    models: dict[str, str]  # model_type -> model name


class UpstreamServiceCreateBody(BaseModel):
    id: str
    base_url: str
    api_key: str


class UpstreamServiceUpdateBody(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class UpstreamModelBody(BaseModel):
    model_type: str  # main | assist | embedding | rerank
    model: str


class UpstreamModelListResponse(BaseModel):
    models: list[str]


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


# ============================================================================
# Upstream LLM Services (服务商 + 模型绑定)
# ============================================================================

_VALID_MODEL_TYPES = {mt.value for mt in ModelType}


async def _service_to_response(
    service: LLMServiceProvider, store: LLMServiceStore
) -> UpstreamServiceResponse:
    models = await store.list_models(service.id)
    return UpstreamServiceResponse(
        id=service.id,
        base_url=service.base_url,
        api_key_masked=service.api_key_masked,
        created_at=service.created_at.isoformat(),
        updated_at=service.updated_at.isoformat(),
        models={m.model_type.value: m.model for m in models},
    )


@router.get("/upstream/services", response_model=list[UpstreamServiceResponse])
async def list_upstream_services(
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """列出全部上游服务商及其模型绑定."""
    services = await store.list_services()
    return [await _service_to_response(s, store) for s in services]


@router.post("/upstream/services", response_model=UpstreamServiceResponse)
async def create_upstream_service(
    body: UpstreamServiceCreateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """新增服务商 (API Key 存加密)."""
    if not body.id.strip() or not body.base_url.strip() or not body.api_key.strip():
        raise HTTPException(status_code=400, detail="id / base_url / api_key 均不可为空")
    service = LLMServiceProvider.create(
        service_id=body.id.strip(),
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
    )
    try:
        await store.save_service(service)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await _service_to_response(service, store)


@router.get("/upstream/services/{service_id}", response_model=UpstreamServiceResponse)
async def get_upstream_service(
    service_id: str,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """获取单个服务商详情."""
    service = await store.get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return await _service_to_response(service, store)


@router.patch("/upstream/services/{service_id}", response_model=UpstreamServiceResponse)
async def update_upstream_service(
    service_id: str,
    body: UpstreamServiceUpdateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """更新 base_url / api_key. 未提供的字段保持不变.

    LLMServiceStore 无直接 update 接口, 只能删后重建 (同 id).
    模型绑定 ON DELETE CASCADE 会随之丢失, 因此先备份再重放.
    """
    old = await store.get_service(service_id)
    if not old:
        raise HTTPException(status_code=404, detail="Service not found")
    new_base = (body.base_url or old.base_url).strip()
    new_key = (body.api_key or old.api_key).strip()
    if not new_base or not new_key:
        raise HTTPException(status_code=400, detail="base_url / api_key 不可为空")

    prev_models = await store.list_models(service_id)
    await store.delete_service(service_id)
    updated = LLMServiceProvider(
        id=service_id,
        base_url=new_base,
        api_key=new_key,
        created_at=old.created_at,
        updated_at=datetime.now(timezone.utc),
    )
    await store.save_service(updated)
    for m in prev_models:
        await store.save_model(m)
    return await _service_to_response(updated, store)


@router.delete("/upstream/services/{service_id}")
async def delete_upstream_service(
    service_id: str,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """删除服务商 (级联删除其模型绑定)."""
    ok = await store.delete_service(service_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"success": True}


@router.post(
    "/upstream/services/{service_id}/models", response_model=UpstreamServiceResponse
)
async def set_upstream_model(
    service_id: str,
    body: UpstreamModelBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """绑定/更新指定角色 (main/assist/embedding/rerank) 的模型名."""
    service = await store.get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if body.model_type not in _VALID_MODEL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"model_type 必须是 {sorted(_VALID_MODEL_TYPES)} 之一",
        )
    config = ModelConfiguration.create(
        service_id=service_id,
        model=body.model.strip(),
        model_type=ModelType(body.model_type),
    )
    try:
        await store.save_model(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await _service_to_response(service, store)


@router.get(
    "/upstream/services/{service_id}/available-models",
    response_model=UpstreamModelListResponse,
)
async def list_upstream_available_models(
    service_id: str,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """调用上游 /v1/models 获取该服务商可用模型列表 (用于下拉选择)."""
    service = await store.get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        config = ForwarderConfig(base_url=service.base_url, api_key=service.api_key)
        async with Forwarder(config) as forwarder:
            models = await forwarder.list_models()
        return UpstreamModelListResponse(models=list(models))
    except UpstreamTimeout as e:
        raise HTTPException(status_code=504, detail=f"上游超时: {e}")
    except UpstreamError as e:
        raise HTTPException(status_code=502, detail=f"上游错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"拉取模型失败: {e}")
