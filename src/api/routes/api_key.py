"""API Key 管理路由."""

from fastapi import APIRouter, HTTPException, status

from ..schemas.api_key import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyListResponse,
    ApiKeyRevokeRequest,
)
from ...storage import ApiKey, ApiKeyStore

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def get_store() -> ApiKeyStore:
    """获取存储实例 (依赖注入)."""
    from ...storage import SqliteApiKeyStore

    store = SqliteApiKeyStore("data/api_keys.db")
    return store


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成 API Key",
    description="生成一个新的 API Key，需要提供备注信息",
)
async def create_api_key(request: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
    """生成新的 API Key.

    Args:
        request: 包含备注信息的请求体

    Returns:
        包含完整 API Key 的响应 (仅在创建时返回一次)

    Raises:
        HTTPException: 创建失败时抛出异常
    """
    store = get_store()
    await store.init_db()

    api_key = ApiKey.generate(note=request.note)
    raw_key = f"sk-{api_key.key_prefix[3:]}"  # 重建完整密钥用于返回

    await store.save(api_key)

    return ApiKeyCreateResponse(
        id=api_key.id,
        key=raw_key,
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
async def list_api_keys() -> ApiKeyListResponse:
    """列出所有 API Key.

    Returns:
        API Key 列表 (仅包含前缀和元数据)
    """
    store = get_store()
    await store.init_db()

    api_keys = await store.list_all()
    items = [
        ApiKeyInfo(
            id=ak.id,
            key_prefix=ak.key_prefix,
            note=ak.note,
            created_at=ak.created_at.isoformat(),
            last_used_at=ak.last_used_at.isoformat() if ak.last_used_at else None,
            is_active=ak.is_active,
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
async def revoke_api_key(key_id: str) -> None:
    """撤销 API Key.

    Args:
        key_id: API Key ID

    Raises:
        HTTPException: 当 API Key 不存在时抛出 404
    """
    store = get_store()
    await store.init_db()

    api_key = await store.get_by_id(key_id)
    if not api_key:
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
async def revoke_api_key_by_request(request: ApiKeyRevokeRequest) -> None:
    """通过请求体撤销 API Key.

    Args:
        request: 包含 key_id 的请求体

    Raises:
        HTTPException: 当 API Key 不存在时抛出 404
    """
    store = get_store()
    await store.init_db()

    api_key = await store.get_by_id(request.key_id)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API Key '{request.key_id}' not found",
        )

    await store.delete(request.key_id)
