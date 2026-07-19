"""管理 API 路由.

提供日志查询、记忆管理、关系状态、Agent 提示词覆盖、仪表盘聚合接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import json
import logging
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.deps import (
    get_api_key_store,
    get_conversation_store,
    get_http_log_store,
    get_llm_service_store,
    get_memory_store,
    get_multi_forwarder,
    get_reindex_progress,
    get_resolver,
    get_vector_store,
)
from src.api.routes.auth import get_current_user
from src.core.config import get_settings
from src.core.models.resolver import RoleResolver
from src.api.schemas.admin import (
    ConversationClearResponse,
    ConversationTurnItem,
    ConversationTurnListResponse,
    PersonaConfigRead,
    PersonaConfigRelation,
    PersonaConfigUpdateBody,
    PersonaResetBody,
    PersonaResetResponse,
    ProbeDimensionBody,
    ProbeDimensionResponse,
    PromptDetail,
    PromptHistoryItem,
    PromptHistoryResponse,
    PromptSummary,
    PromptValidateResponse,
    PromptWriteBody,
    PruneBreakdown as PruneBreakdownSchema,
    PruneResponse,
    PruneStartBody,
    ReindexStartBody,
    ReindexStatusResponse,
    RoleBindingAddBody,
    RoleBindingItem,
    RoleBindingListResponse,
    RoleBindingReorderBody,
)
from src.core.config import (
    _delete_persona_override,
    _load_persona_override,
    _reset_settings,
    _write_persona_override,
    get_settings,
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
from src.persistence.conversation_store import SqliteConversationStore
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
    page: int = 1
    page_size: int = 50


class RelationshipResponse(BaseModel):
    persona_id: str
    user_id: str
    intimacy: float
    trust: float
    relationship_type: Optional[str]
    notes: Optional[str]
    updated_at: str
    # v0.2.10: 动态称呼演化. 序列化时保证非 None (若表中为 NULL 则填 TOML 基线值),
    # 前端拿到的永远是"当前有效值".
    persona_addressing: str
    user_addressing: str
    context: str


class RelationshipUpdateBody(BaseModel):
    """v0.2.10: 人工 override 关系称呼/背景 (source='manual').

    三字段都可选传, 但至少一个非 None. reason 必填 (至少 5 字), 用于审计.
    """

    persona_addressing: Optional[str] = None
    user_addressing: Optional[str] = None
    context: Optional[str] = None
    reason: str = Field(..., min_length=5, max_length=500)
    user_id: str = "default"


class RelationshipAuditItem(BaseModel):
    id: int
    changed_at: str
    source: str
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    reason: str


class RelationshipAuditResponse(BaseModel):
    items: list[RelationshipAuditItem]


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
    try:
        pkg_version = _pkg_version("mnemosync")
    except PackageNotFoundError:
        pkg_version = "0.0.0+unknown"
    return HealthResponse(
        status="ok",
        version=pkg_version,
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
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    memory_type: str | None = Query(None, description="normal | permanent"),
    sort_by: str = Query("created_at", description="created_at | last_accessed | importance | decay_rate | access_count | memory_type | source_user"),
    sort_order: str = Query("desc", description="asc | desc"),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """查询记忆列表 (服务器端分页 + 排序).

    total 是符合 source_user + memory_type 过滤的**全量匹配数**, 不是本页返回条数;
    前端据此计算总页数。sort_by 走白名单, 非法值退回 created_at。
    """
    offset = (page - 1) * page_size
    items, total = await store.list_page_for_user(
        source_user,
        limit=page_size,
        offset=offset,
        memory_type=memory_type,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return MemoryListResponse(
        items=[_memory_to_response(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
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
    """获取关系状态.

    关系尚未建立 (新装 / 人格重置后 / 与新 user_id 首次交互) 时返回默认 stranger/0/0,
    不落库 — 后续对话时 `lifecycle.update_relationship` 会自然创建真实行。

    v0.2.10: 响应含 persona_addressing / user_addressing / context. 表中为 NULL 时
    用 settings.persona.relation.* 基线填充, 前端拿到的永远是"当前有效值".
    """
    settings = get_settings()
    base = settings.persona.relation
    rel = await store.get_relationship("default", user_id)
    if not rel:
        return RelationshipResponse(
            persona_id="default",
            user_id=user_id,
            intimacy=0.0,
            trust=0.0,
            relationship_type="stranger",
            notes=None,
            updated_at="",
            persona_addressing=base.persona_addressing,
            user_addressing=base.user_addressing,
            context=base.context,
        )

    return RelationshipResponse(
        persona_id=rel.persona_id,
        user_id=rel.user_id,
        intimacy=rel.intimacy_score,
        trust=rel.trust_level,
        relationship_type=rel.type,
        notes=rel.notes,
        updated_at=rel.last_active.isoformat() if rel.last_active else "",
        persona_addressing=rel.persona_addressing or base.persona_addressing,
        user_addressing=rel.user_addressing or base.user_addressing,
        context=rel.context or base.context,
    )


@router.put("/relationship", response_model=RelationshipResponse)
async def update_relationship_addressing(
    body: RelationshipUpdateBody,
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """人工 override 关系称呼/背景 (source='manual').

    v0.2.10: 允许通过面板/CLI 修改 persona_addressing / user_addressing / context.
    - 三字段可选, 至少传一个非 None
    - reason 必填 (min 5), 写入审计日志
    - 相同值会被跳过, 不写 audit
    - 相同响应 shape 与 GET 一致
    """
    provided = {
        "persona_addressing": body.persona_addressing,
        "user_addressing": body.user_addressing,
        "context": body.context,
    }
    if all(v is None for v in provided.values()):
        raise HTTPException(400, detail="至少需要传入一个字段")
    try:
        await store.update_relationship_addressing(
            "default", body.user_id,
            persona_addressing=body.persona_addressing,
            user_addressing=body.user_addressing,
            context=body.context,
            source="manual",
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return await get_relationship(user_id=body.user_id, store=store)


@router.get("/relationship/audit", response_model=RelationshipAuditResponse)
async def list_relationship_audit(
    user_id: str = "default",
    limit: int = Query(20, ge=1, le=200),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """按时间倒序返回关系称呼字段的审计条目 (v0.2.10)."""
    entries = await store.list_relationship_audit("default", user_id, limit=limit)
    return RelationshipAuditResponse(
        items=[
            RelationshipAuditItem(
                id=e.id,
                changed_at=e.changed_at.isoformat(),
                source=e.source,
                field_name=e.field_name,
                old_value=e.old_value,
                new_value=e.new_value,
                reason=e.reason,
            )
            for e in entries
        ]
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


# ============================================================================
# Role Bindings (v0.2.3 起模型绑定单一真相源)
# ============================================================================


def _parse_role(role: str) -> ModelType:
    try:
        return ModelType(role)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"role 必须是 {sorted(_VALID_MODEL_TYPES)} 之一",
        )


def _binding_to_item(b) -> RoleBindingItem:
    return RoleBindingItem(
        role=b.role.value,
        priority=b.priority,
        service_id=b.service_id,
        model=b.model,
        created_at=b.created_at.isoformat(),
        context_length=b.context_length,
        embedding_dim=b.embedding_dim,
        send_dimensions=b.send_dimensions,
    )


@router.get("/model-bindings", response_model=RoleBindingListResponse)
async def list_model_bindings(
    role: Optional[str] = None,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """列出角色绑定. role 省略时返回所有角色."""
    role_enum = _parse_role(role) if role else None
    bindings = await store.list_role_bindings(role_enum)
    return RoleBindingListResponse(items=[_binding_to_item(b) for b in bindings])


@router.post("/model-bindings", response_model=RoleBindingItem)
async def add_model_binding(
    body: RoleBindingAddBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
):
    """追加一条角色绑定. priority 省略排到末尾, 指定时后续条目自动让位.

    嵌入角色只允许一条绑定, 重复添加返回 409 (需先删除现有绑定).
    """
    role_enum = _parse_role(body.role)
    if not body.service_id.strip() or not body.model.strip():
        raise HTTPException(status_code=400, detail="service_id / model 不可为空")
    try:
        binding = await store.add_role_binding(
            role_enum,
            body.service_id.strip(),
            body.model.strip(),
            priority=body.priority,
            context_length=body.context_length,
            embedding_dim=body.embedding_dim,
            send_dimensions=body.send_dimensions,
        )
    except ValueError as e:
        msg = str(e)
        if "嵌入模型只允许一条绑定" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    resolver.invalidate(role_enum)
    return _binding_to_item(binding)


@router.delete("/model-bindings/{role}/{priority}")
async def delete_model_binding(
    role: str,
    priority: int,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
):
    """删除一条绑定, 后续条目 priority 前移."""
    role_enum = _parse_role(role)
    ok = await store.delete_role_binding(role_enum, priority)
    if not ok:
        raise HTTPException(status_code=404, detail="binding not found")
    resolver.invalidate(role_enum)
    return {"success": True}


@router.put("/model-bindings/{role}/reorder", response_model=RoleBindingListResponse)
async def reorder_model_bindings(
    role: str,
    body: RoleBindingReorderBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
):
    """按新优先级重排角色的全部绑定. order 必须与现有绑定完全一一对应."""
    role_enum = _parse_role(role)
    try:
        bindings = await store.reorder_role_bindings(
            role_enum, [(sid, m) for sid, m in body.order]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    resolver.invalidate(role_enum)
    return RoleBindingListResponse(items=[_binding_to_item(b) for b in bindings])


@router.post("/model-bindings/probe-dimension", response_model=ProbeDimensionResponse)
async def probe_embedding_dimension(
    body: ProbeDimensionBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
):
    """临时调用嵌入模型探测输出维度. 不落库, 仅用于面板 Add/Replace 对话框填值.

    - 不校验绑定是否存在 (允许"先探测再绑定")
    - service_id 必须已存在于 llm_services 表
    - 用户可传 dimensions (DashScope v3 等可变维模型) 或不传 (走上游默认)
    """
    if not body.service_id.strip() or not body.model.strip():
        raise HTTPException(status_code=400, detail="service_id / model 不可为空")
    svc = await store.get_service(body.service_id.strip())
    if svc is None:
        raise HTTPException(status_code=404, detail=f"service '{body.service_id}' 不存在")
    fwd = Forwarder(
        ForwarderConfig(
            base_url=svc.base_url,
            api_key=svc.api_key,
            default_model=body.model.strip(),
        )
    )
    try:
        vecs = await fwd.embed(
            input="hi",
            model=body.model.strip(),
            dimensions=body.dimensions,
        )
    except UpstreamError as e:
        raise HTTPException(
            status_code=502,
            detail=f"上游探测失败 ({e.status_code}): {e}",
        )
    except UpstreamTimeout as e:
        raise HTTPException(status_code=504, detail=f"上游探测超时: {e}")
    finally:
        await fwd.close()
    if not vecs or not vecs[0]:
        raise HTTPException(status_code=502, detail="上游返回空向量")
    return ProbeDimensionResponse(dimensions=len(vecs[0]))


# ============================================================================
# Memory reindex + prune (v0.2.4)
# ============================================================================


@router.post("/memory/reindex", response_model=ReindexStatusResponse)
async def start_memory_reindex(
    body: ReindexStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    forwarder=Depends(get_multi_forwarder),
    resolver: RoleResolver = Depends(get_resolver),
    progress=Depends(get_reindex_progress),
):
    """启动向量库重建 (异步背景任务). 已在运行返回 409."""
    from src.core.memory.reindex import Reindexer

    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 已在运行中")

    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver, progress)

    import asyncio as _asyncio

    async def _run():
        try:
            await reindexer.run(
                prune=body.prune,
                priority_threshold=body.priority_threshold,
            )
        except Exception as e:
            logger.error("reindex 背景任务失败: %s", e)

    _asyncio.create_task(_run())
    return ReindexStatusResponse(**progress.snapshot())


@router.get("/memory/reindex/status", response_model=ReindexStatusResponse)
async def get_memory_reindex_status(
    progress=Depends(get_reindex_progress),
):
    return ReindexStatusResponse(**progress.snapshot())


@router.post("/memory/prune", response_model=PruneResponse)
async def prune_memories(
    body: PruneStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    progress=Depends(get_reindex_progress),
):
    """按衰减规则清理记忆. 与 reindex 互斥 (running 时返 409).

    PERMANENT 永不删; is_forgotten / expired / priority<threshold 命中删除.
    dry_run=true 只返回统计。
    """
    from src.core.memory.reindex import Pruner

    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 运行中, prune 暂不可执行")

    pruner = Pruner(memory_store, vector_store)
    result = await pruner.run(
        priority_threshold=body.priority_threshold,
        dry_run=body.dry_run,
    )
    return PruneResponse(
        total_before=result.total_before,
        would_delete=result.would_delete,
        deleted=result.deleted,
        breakdown=PruneBreakdownSchema(**result.breakdown.as_dict()),
    )


# ============================================================================
# 跨前端对话流水 (v0.2.6 短期记忆)
# ============================================================================


@router.get("/conversation-turns", response_model=ConversationTurnListResponse)
async def list_conversation_turns(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    role: str | None = Query(None, description="user | assistant, 省略=全部"),
    source_frontend: str | None = Query(
        None, description="精确匹配来源标签 (api_key.note), 省略=全部"
    ),
    sort_by: str = Query("ts", description="ts | role | token_count | source_frontend | id"),
    sort_order: str = Query("desc", description="asc | desc"),
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """按 ts 降序分页列出跨前端对话流水.

    面板 "上下文流水" 视图用. 服务器把所有前端的对话汇聚到这里, 装填时
    按时间窗 + 模型窗双窗口从这里裁剪. sort_by 走白名单, 非法值退回 ts。
    """
    offset = (page - 1) * page_size
    turns, total = await store.list_page(
        limit=page_size,
        offset=offset,
        role=role,
        source_frontend=source_frontend,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = [
        ConversationTurnItem(
            id=t.id or 0,
            role=t.role,
            content=t.content,
            ts=t.ts.isoformat(),
            token_count=t.token_count,
            source_frontend=t.source_frontend,
        )
        for t in turns
    ]
    return ConversationTurnListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/conversation-turns/sources")
async def list_conversation_turn_sources(
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """列出流水里出现过的所有来源标签 (source_frontend distinct).

    面板 "来源" 列 header filter 用. NULL / 空串排除 (视为 "未标注")。
    """
    return {"items": await store.list_source_frontends()}


@router.delete("/conversation-turns/{turn_id}")
async def delete_conversation_turn(
    turn_id: int,
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """删除单条对话轮次. 用于面板逐条清理.

    注意: 不动其它前端已经拿到的上下文; 但下一次装填时该条从服务器视角"从未存在"。
    """
    ok = await store.delete_by_id(turn_id)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation turn not found")
    return {"success": True, "id": turn_id}


@router.delete("/conversation-turns", response_model=ConversationClearResponse)
async def clear_conversation_turns(
    since_iso: str | None = Query(None, alias="since", description="ISO 时间, 只清早于该时间的记录 (省略则全清)"),
    store: SqliteConversationStore = Depends(get_conversation_store),
):
    """清空跨前端对话流水.

    * 不带 since → 全清 ("重置连续记忆")
    * 带 since → 只清 ts < since (面板 "只留最近 3 天")

    注意: 这不是"客户端 UI 清空对话" — 那种只是前端自己的显示状态, 服务器
    仍保有连续记忆. 只有面板操作或本端点才能真正把服务器的流水抹掉。
    """
    if since_iso:
        try:
            cutoff = datetime.fromisoformat(since_iso)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid since: {since_iso!r}")
        deleted = await store.delete_before(cutoff)
    else:
        deleted = await store.delete_all()
    return ConversationClearResponse(deleted=deleted)


# ============================================================================
# 人格状态重置 (v0.2.7) —— 回到"新装"语义
# ============================================================================


@router.post("/persona/reset", response_model=PersonaResetResponse)
async def reset_persona(
    body: PersonaResetBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    conversation_store: SqliteConversationStore = Depends(get_conversation_store),
    progress=Depends(get_reindex_progress),
):
    """把人格状态回退到"新装"级别: 清空长期记忆 (含 PERMANENT) / 关系 / 短期流水 / 向量库.

    与 prune 的差异:
      * prune 保 PERMANENT, 只按衰减规则清 NORMAL; 此端点**不保 PERMANENT**
      * prune 不动 relationships / conversation_turns; 此端点一并清空
      * 向量库通过 reset_collection() 整个 drop 重建 (下次写入自动重锁 embedding metadata)

    保留:
      * api_keys / auth / llm_service (含 role_bindings) / prompts 覆盖层 / http_logs
      * config.local.toml 里的 [persona] 定义 (是"人格描述", 不是"人格状态")

    与 reindex 互斥: running 时返 409.

    dry_run=True 只统计不执行. 非 dry_run 任一步失败其他步骤已完成的不回滚,
    错误累计到 errors 便于面板呈现部分失败.
    """
    if progress.is_running():
        raise HTTPException(
            status_code=409, detail="reindex 运行中, persona reset 暂不可执行"
        )

    mem_count = await memory_store.count_all()
    rel_count = await memory_store.count_relationships()
    turn_count = await conversation_store.count()

    if body.dry_run:
        return PersonaResetResponse(
            dry_run=True,
            deleted_memories=mem_count,
            deleted_relationships=rel_count,
            deleted_conversation_turns=turn_count,
            vector_reset=False,
        )

    errors: list[str] = []
    vector_reset = False
    deleted_memories = 0
    deleted_relationships = 0
    deleted_turns = 0

    # 1. 先清 Chroma (若失败中止, 尚未破坏 SQLite)
    try:
        vector_store.reset_collection()
        vector_reset = True
    except Exception as e:
        logger.exception("persona reset: vector reset 失败")
        errors.append(f"vector_reset: {e}")

    # 2. memory_entries (含 PERMANENT)
    try:
        deleted_memories = await memory_store.delete_all_memories()
    except Exception as e:
        logger.exception("persona reset: memory_entries 清空失败")
        errors.append(f"memory_entries: {e}")

    # 3. relationships
    try:
        deleted_relationships = await memory_store.delete_all_relationships()
    except Exception as e:
        logger.exception("persona reset: relationships 清空失败")
        errors.append(f"relationships: {e}")

    # 4. conversation_turns (短期记忆流水)
    try:
        deleted_turns = await conversation_store.delete_all()
    except Exception as e:
        logger.exception("persona reset: conversation_turns 清空失败")
        errors.append(f"conversation_turns: {e}")

    logger.info(
        "persona reset 完成: memories=%d relationships=%d turns=%d vector=%s errors=%d",
        deleted_memories, deleted_relationships, deleted_turns, vector_reset, len(errors),
    )
    return PersonaResetResponse(
        dry_run=False,
        deleted_memories=deleted_memories,
        deleted_relationships=deleted_relationships,
        deleted_conversation_turns=deleted_turns,
        vector_reset=vector_reset,
        errors=errors,
    )


# ============================================================================
# 人格配置编辑 (v0.2.11) —— 覆盖 data/persona_override.toml, 热重载
# ============================================================================


def _build_persona_read() -> PersonaConfigRead:
    """从多层合并后的 settings 构建响应."""
    s = get_settings()
    rel = s.persona.relation
    override_exists = _load_persona_override() is not None
    return PersonaConfigRead(
        name=s.persona.name,
        prompt=s.persona.prompt,
        relation=PersonaConfigRelation(
            persona_addressing=rel.persona_addressing,
            user_addressing=rel.user_addressing,
            context=rel.context,
        ),
        overridden=override_exists,
    )


@router.get("/persona", response_model=PersonaConfigRead)
async def get_persona_config():
    """获取当前人格配置 (多层合并后). 不返回 TOML 原始内容, 返回解析后的字段."""
    return _build_persona_read()


@router.put("/persona", response_model=PersonaConfigRead)
async def update_persona_config(body: PersonaConfigUpdateBody):
    """写入 data/persona_override.toml, 覆盖人格字段.

    三字段都可选传, 但至少传一个. 写入后立即热重载 (调用 _reset_settings).
    """
    if body.name is None and body.prompt is None and body.relation is None:
        raise HTTPException(400, detail="至少需要传入一个字段")

    # 读取当前 override (若存在) 作为增量基础
    current = _load_persona_override() or {}
    if body.name is not None:
        current["name"] = body.name
    if body.prompt is not None:
        current["prompt"] = body.prompt
    if body.relation is not None:
        current_rel = dict(current.get("relation", {}))
        if body.relation.persona_addressing is not None:
            current_rel["persona_addressing"] = body.relation.persona_addressing
        if body.relation.user_addressing is not None:
            current_rel["user_addressing"] = body.relation.user_addressing
        if body.relation.context is not None:
            current_rel["context"] = body.relation.context
        current["relation"] = current_rel

    _write_persona_override(current)
    _reset_settings()
    return _build_persona_read()


@router.delete("/persona", response_model=PersonaConfigRead)
async def reset_persona_config():
    """删除 persona override 文件, 回退到 config.local.toml / 资源默认值."""
    _delete_persona_override()
    _reset_settings()
    return _build_persona_read()

