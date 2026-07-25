"""API Key 管理路由."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_api_key_store
from src.persistence.api_key_store import (
    API_KEY_SOURCE_USER,
    ApiKey,
    SqliteApiKeyStore,
)

from ..schemas.api_key import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyListResponse,
    ApiKeyRevokeRequest,
)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成 API Key",
    description="生成一个新的 API Key，需要提供备注信息",
)
async def create_api_key(
    request: ApiKeyCreateRequest,
    store: SqliteApiKeyStore = Depends(get_api_key_store),
) -> ApiKeyCreateResponse:
    """生成新的 API Key."""
    api_key = ApiKey.generate(note=request.note, strategy_id=request.strategy_id)
    await store.save(api_key)

    return ApiKeyCreateResponse(
        id=api_key.id,
        key=api_key.key_full,
        key_prefix=api_key.key_prefix,
        note=api_key.note,
        created_at=api_key.created_at.isoformat(),
    )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    summary="列出所有 API Key",
    description="获取所有 API Key 的信息 (不包含完整密钥)",
)
async def list_api_keys(
    store: SqliteApiKeyStore = Depends(get_api_key_store),
) -> ApiKeyListResponse:
    """列出所有 API Key. 只返回用户手动创建的 (source='user'), 调试面板自动生成的不暴露."""
    api_keys = await store.list_all(source=API_KEY_SOURCE_USER)
    items = [
        ApiKeyInfo(
            id=ak.id,
            key=ak.key_full,
            key_prefix=ak.key_prefix,
            note=ak.note,
            created_at=ak.created_at.isoformat(),
            last_used_at=ak.last_used_at.isoformat() if ak.last_used_at else None,
            is_active=ak.is_active,
            strategy_id=ak.strategy_id,
        )
        for ak in api_keys
    ]

    return ApiKeyListResponse(items=items)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撤销 API Key",
    description="根据 ID 撤销 (删除) 一个 API Key",
)
async def revoke_api_key(
    key_id: str,
    store: SqliteApiKeyStore = Depends(get_api_key_store),
) -> None:
    """撤销 API Key."""
    api_key = await store.get_by_id(key_id)
    if not api_key or api_key.source != API_KEY_SOURCE_USER:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API Key '{key_id}' not found",
        )

    await store.delete(key_id)


@router.post(
    "/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撤销 API Key (通过请求体)",
    description="通过请求体中的 key_id 撤销 API Key",
)
async def revoke_api_key_by_request(
    request: ApiKeyRevokeRequest,
    store: SqliteApiKeyStore = Depends(get_api_key_store),
) -> None:
    """通过请求体撤销 API Key."""
    api_key = await store.get_by_id(request.key_id)
    if not api_key or api_key.source != API_KEY_SOURCE_USER:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API Key '{request.key_id}' not found",
        )

    await store.delete(request.key_id)
