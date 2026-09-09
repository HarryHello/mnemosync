"""管理 API 路由 - 模型注册表 (v0.4.1, RFC model-registry).

模型作为一等实体: 服务商下登记模型 (id = "{service_id}:{model}"), 能力字段
(模态/上下文/嵌入维) 与并发数归属模型; 角色绑定只引用 model_id.

**认证**: 所有路由要求登录 (Depends(get_current_user)), 由父 router 统一注入.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import get_llm_service_store
from src.infra.llm_service.models import ModelRegistryEntry
from src.infra.llm_service.store import LLMServiceStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
)


# ============================================================================
# Schemas
# ============================================================================


class ModelRegistryItem(BaseModel):
    id: str
    service_id: str
    model: str
    display_name: str | None
    input_modalities: list[str]
    output_modalities: list[str]
    context_length: int | None
    output_limit: int | None
    supports_tools: bool
    embedding_dim: int | None
    send_dimensions: bool
    concurrency: int
    enabled: bool
    model_kind: str = "chat"
    created_at: str
    updated_at: str


class ModelRegistryCreateBody(BaseModel):
    service_id: str
    model: str
    display_name: str | None = None
    input_modalities: list[str] = ["text"]
    output_modalities: list[str] = ["text"]
    context_length: int | None = Field(default=None, ge=1)
    output_limit: int | None = Field(default=None, ge=1)
    supports_tools: bool = False
    embedding_dim: int | None = Field(default=None, ge=1)
    send_dimensions: bool = False
    concurrency: int = Field(default=20, ge=0, description="并发上限, 0 = 不限")
    enabled: bool = True
    model_kind: str = "chat"  # chat | embedding | rerank


class ModelRegistryUpdateBody(BaseModel):
    display_name: str | None = None
    clear_display_name: bool = False
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    context_length: int | None = Field(default=None, ge=1)
    clear_context_length: bool = False
    output_limit: int | None = Field(default=None, ge=1)
    clear_output_limit: bool = False
    supports_tools: bool | None = None
    embedding_dim: int | None = Field(default=None, ge=1)
    clear_embedding_dim: bool = False
    send_dimensions: bool | None = None
    concurrency: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    model_kind: str | None = None

    model_config = {"protected_namespaces": ()}


class ModelImportItem(BaseModel):
    """带能力的待导入模型 (来自上游 /v1/models 解析)."""

    model: str
    display_name: str | None = None
    context_length: int | None = Field(default=None, ge=1)
    output_limit: int | None = Field(default=None, ge=1)
    supports_tools: bool = False
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])


class ModelImportBody(BaseModel):
    service_id: str
    models: list[ModelImportItem]


class ModelImportResponse(BaseModel):
    added: int
    skipped: int


# ============================================================================
# Helpers
# ============================================================================


def _entry_to_item(e: ModelRegistryEntry) -> ModelRegistryItem:
    return ModelRegistryItem(
        id=e.id,
        service_id=e.service_id,
        model=e.model,
        display_name=e.display_name,
        input_modalities=e.input_modalities,
        output_modalities=e.output_modalities,
        context_length=e.context_length,
        output_limit=e.output_limit,
        supports_tools=e.supports_tools,
        embedding_dim=e.embedding_dim,
        send_dimensions=e.send_dimensions,
        concurrency=e.concurrency,
        enabled=e.enabled,
        model_kind=e.model_kind,
        created_at=e.created_at.isoformat(),
        updated_at=e.updated_at.isoformat(),
    )


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/models", response_model=list[ModelRegistryItem])
async def list_registry_models(
    service_id: str | None = None,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> list[ModelRegistryItem]:
    """模型注册表列表 (可选按服务商过滤)."""
    entries = await store.list_model_registry(service_id=service_id)
    return [_entry_to_item(e) for e in entries]


@router.post("/models", response_model=ModelRegistryItem)
async def create_registry_model(
    body: ModelRegistryCreateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> ModelRegistryItem:
    """注册一个模型. 同服务商同名已存在返回 409."""
    model = body.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model 不可为空")
    existing = await store.get_model_registry(f"{body.service_id}:{model}")
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"模型已存在: {body.service_id}:{model} (可 PATCH 更新或先删除)",
        )
    entry = ModelRegistryEntry.create(
        body.service_id,
        model,
        display_name=body.display_name,
        input_modalities=body.input_modalities,
        output_modalities=body.output_modalities,
        context_length=body.context_length,
        output_limit=body.output_limit,
        supports_tools=body.supports_tools,
        embedding_dim=body.embedding_dim,
        send_dimensions=body.send_dimensions,
        concurrency=body.concurrency,
        enabled=body.enabled,
        model_kind=body.model_kind,
    )
    try:
        await store.save_model_registry(entry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _entry_to_item(entry)


# {model_id:path}: 注册表 id 形如 {service}:{model}, 模型名可含 '/'
# (OpenRouter 形态 deepseek/deepseek-v4-flash), 默认 converter 不匹配斜杠会 404.
@router.patch("/models/{model_id:path}", response_model=ModelRegistryItem)
async def update_registry_model(
    model_id: str,
    body: ModelRegistryUpdateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> ModelRegistryItem:
    """就地更新注册表条目 (显示名/能力/并发/启用)."""
    provided = body.model_dump(exclude_unset=True)
    kwargs: dict[str, Any] = {}
    for key in (
        "clear_display_name", "input_modalities", "output_modalities",
        "clear_context_length", "clear_output_limit", "supports_tools",
        "clear_embedding_dim", "send_dimensions", "concurrency", "enabled",
        "model_kind",
    ):
        if key in provided:
            kwargs[key] = provided[key]
    if "display_name" in provided:
        dn = provided["display_name"]
        if dn is None or dn == "":
            kwargs["clear_display_name"] = True
        else:
            kwargs["display_name"] = dn
    if "context_length" in provided:
        cl = provided["context_length"]
        if cl is None:
            kwargs["clear_context_length"] = True
        else:
            kwargs["context_length"] = cl
    if "embedding_dim" in provided:
        ed = provided["embedding_dim"]
        if ed is None:
            kwargs["clear_embedding_dim"] = True
        else:
            kwargs["embedding_dim"] = ed
    if "output_limit" in provided:
        ol = provided["output_limit"]
        if ol is None:
            kwargs["clear_output_limit"] = True
        else:
            kwargs["output_limit"] = ol

    try:
        entry = await store.update_model_registry(model_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="model not found")
    return _entry_to_item(entry)


@router.delete("/models/{model_id:path}")
async def delete_registry_model(
    model_id: str,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> dict[str, Any]:
    """删除注册表条目. 被角色绑定引用时返回 409 (需先解绑)."""
    try:
        ok = await store.delete_model_registry(model_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="model not found")
    return {"success": True}


@router.post("/models:import", response_model=ModelImportResponse)
async def import_registry_models(
    body: ModelImportBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> ModelImportResponse:
    """从上游模型列表批量导入注册表 (含能力解析结果). 已存在跳过."""
    entries = [
        ModelRegistryEntry.create(
            body.service_id,
            item.model.strip(),
            display_name=item.display_name,
            context_length=item.context_length,
            output_limit=item.output_limit,
            supports_tools=item.supports_tools,
            input_modalities=item.input_modalities,
            output_modalities=item.output_modalities,
        )
        for item in body.models
        if item.model and item.model.strip()
    ]
    try:
        added, skipped = await store.import_model_registry(body.service_id, entries)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ModelImportResponse(added=added, skipped=skipped)
