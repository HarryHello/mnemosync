"""管理 API 路由 - 上游 LLM 服务商 + 模型绑定.

提供服务商 CRUD、模型绑定、维度探测、可用模型查询接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import (
    get_llm_service_store,
    get_resolver,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    ProbeDimensionBody,
    ProbeDimensionResponse,
    RoleBindingAddBody,
    RoleBindingItem,
    RoleBindingListResponse,
    RoleBindingReorderBody,
    RoleBindingUpdateBody,
)
from src.core.models.resolver import RoleResolver
from src.infra.forwarder import Forwarder, ForwarderConfig, UpstreamError, UpstreamTimeout
from src.infra.llm_service.models import (
    ApiFormat,
    LLMServiceProvider,
    ModelConfiguration,
    ModelType,
    RoleBinding,
)
from src.infra.llm_service.store import LLMServiceStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Schemas
# ============================================================================


class UpstreamServiceResponse(BaseModel):
    id: str
    base_url: str
    api_key_masked: str
    api_format: str = "openai"
    created_at: str
    updated_at: str
    models: dict[str, str]  # model_type -> model name


class UpstreamServiceCreateBody(BaseModel):
    id: str
    base_url: str
    api_key: str
    api_format: str = "openai"  # openai | anthropic | responses


class UpstreamServiceUpdateBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    api_format: str | None = None


# 合法的上游 API 格式
VALID_API_FORMATS = ("openai", "anthropic", "responses")


class UpstreamModelBody(BaseModel):
    model_type: str  # main | assist | embedding | rerank
    model: str


class UpstreamModelListResponse(BaseModel):
    models: list[str]


# ============================================================================
# Helpers
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
        api_format=service.api_format,
        created_at=service.created_at.isoformat(),
        updated_at=service.updated_at.isoformat(),
        models={m.model_type.value: m.model for m in models},
    )


def _parse_role(role: str) -> ModelType:
    try:
        return ModelType(role)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"role 必须是 {sorted(_VALID_MODEL_TYPES)} 之一",
        )


def _binding_to_item(b: RoleBinding) -> RoleBindingItem:
    return RoleBindingItem(
        role=b.role.value,
        priority=b.priority,
        service_id=b.service_id,
        model=b.model,
        created_at=b.created_at.isoformat(),
        context_length=b.context_length,
        embedding_dim=b.embedding_dim,
        send_dimensions=b.send_dimensions,
        input_modalities=b.input_modalities,
        output_modalities=b.output_modalities,
    )


# ============================================================================
# Upstream Services
# ============================================================================


@router.get("/upstream/services", response_model=list[UpstreamServiceResponse])
async def list_upstream_services(
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> list[UpstreamServiceResponse]:
    """列出全部上游服务商及其模型绑定."""
    services = await store.list_services()
    return [await _service_to_response(s, store) for s in services]


@router.post("/upstream/services", response_model=UpstreamServiceResponse)
async def create_upstream_service(
    body: UpstreamServiceCreateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> UpstreamServiceResponse:
    """新增服务商 (API Key 存加密)."""
    if not body.id.strip() or not body.base_url.strip() or not body.api_key.strip():
        raise HTTPException(status_code=400, detail="id / base_url / api_key 均不可为空")
    api_format = cast(
        ApiFormat, body.api_format if body.api_format in VALID_API_FORMATS else "openai"
    )
    service = LLMServiceProvider.create(
        service_id=body.id.strip(),
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        api_format=api_format,
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
) -> UpstreamServiceResponse:
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
) -> UpstreamServiceResponse:
    """更新 base_url / api_key. 未提供的字段保持不变.

    LLMServiceStore 无直接 update 接口, 只能删后重建 (同 id).
    模型绑定 ON DELETE CASCADE 会随之丢失, 因此先备份再重放.
    """
    old = await store.get_service(service_id)
    if not old:
        raise HTTPException(status_code=404, detail="Service not found")
    new_base = (body.base_url or old.base_url).strip()
    new_key = (body.api_key or old.api_key).strip()
    new_format = cast(
        ApiFormat, body.api_format if body.api_format in VALID_API_FORMATS else old.api_format
    )
    if not new_base or not new_key:
        raise HTTPException(status_code=400, detail="base_url / api_key 不可为空")

    prev_models = await store.list_models(service_id)
    await store.delete_service(service_id)
    updated = LLMServiceProvider(
        id=service_id,
        base_url=new_base,
        api_key=new_key,
        api_format=new_format,
        created_at=old.created_at,
        updated_at=datetime.now(UTC),
    )
    await store.save_service(updated)
    for m in prev_models:
        await store.save_model(m)
    return await _service_to_response(updated, store)


@router.delete("/upstream/services/{service_id}")
async def delete_upstream_service(
    service_id: str,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> dict[str, Any]:
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
) -> UpstreamServiceResponse:
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
) -> UpstreamModelListResponse:
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


@router.get("/model-bindings", response_model=RoleBindingListResponse)
async def list_model_bindings(
    role: str | None = None,
    store: LLMServiceStore = Depends(get_llm_service_store),
) -> RoleBindingListResponse:
    """列出角色绑定. role 省略时返回所有角色."""
    role_enum = _parse_role(role) if role else None
    bindings = await store.list_role_bindings(role_enum)
    return RoleBindingListResponse(items=[_binding_to_item(b) for b in bindings])


@router.post("/model-bindings", response_model=RoleBindingItem)
async def add_model_binding(
    body: RoleBindingAddBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
) -> RoleBindingItem:
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
            input_modalities=body.input_modalities,
            output_modalities=body.output_modalities,
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
) -> dict[str, Any]:
    """删除一条绑定, 后续条目 priority 前移."""
    role_enum = _parse_role(role)
    ok = await store.delete_role_binding(role_enum, priority)
    if not ok:
        raise HTTPException(status_code=404, detail="binding not found")
    resolver.invalidate(role_enum)
    return {"success": True}


@router.patch("/model-bindings/{role}/{priority}", response_model=RoleBindingItem)
async def update_model_binding(
    role: str,
    priority: int,
    body: RoleBindingUpdateBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
) -> RoleBindingItem:
    """就地更新一条绑定的可编辑字段.

    - role / priority 由 URL 定位, 不可改; 调整顺序请走 reorder
    - 只有请求体里显式出现的字段会被覆盖 (exclude_unset)
    - context_length / embedding_dim 显式传 null 表示清空
    - service_id / model 非法或为空字符串会被拒绝
    """
    role_enum = _parse_role(role)
    provided = body.model_dump(exclude_unset=True)

    kwargs: dict[str, Any] = {}
    if "service_id" in provided:
        sid = provided["service_id"]
        if sid is None or not sid.strip():
            raise HTTPException(status_code=400, detail="service_id 不可为空")
        kwargs["service_id"] = sid.strip()
    if "model" in provided:
        m = provided["model"]
        if m is None or not m.strip():
            raise HTTPException(status_code=400, detail="model 不可为空")
        kwargs["model"] = m.strip()
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
    if "send_dimensions" in provided:
        kwargs["send_dimensions"] = bool(provided["send_dimensions"])
    if "input_modalities" in provided:
        kwargs["input_modalities"] = provided["input_modalities"]
    if "output_modalities" in provided:
        kwargs["output_modalities"] = provided["output_modalities"]

    if not kwargs:
        raise HTTPException(status_code=400, detail="没有可更新的字段")

    try:
        binding = await store.update_role_binding(role_enum, priority, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if binding is None:
        raise HTTPException(status_code=404, detail="binding not found")
    resolver.invalidate(role_enum)
    return _binding_to_item(binding)


@router.put("/model-bindings/{role}/reorder", response_model=RoleBindingListResponse)
async def reorder_model_bindings(
    role: str,
    body: RoleBindingReorderBody,
    store: LLMServiceStore = Depends(get_llm_service_store),
    resolver: RoleResolver = Depends(get_resolver),
) -> RoleBindingListResponse:
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
) -> ProbeDimensionResponse:
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
