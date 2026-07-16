"""API Key 相关的 Pydantic 模式."""

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    """创建 API Key 请求."""

    note: str = Field(..., min_length=1, max_length=256, description="API Key 备注")


class ApiKeyCreateResponse(BaseModel):
    """创建 API Key 响应."""

    id: str = Field(..., description="API Key ID")
    key: str = Field(..., description="API Key (仅在创建时返回，请妥善保管)")
    key_prefix: str = Field(..., description="API Key 前缀 (用于识别)")
    note: str = Field(..., description="备注")
    created_at: str = Field(..., description="创建时间")


class ApiKeyInfo(BaseModel):
    """API Key 信息.

    ``key`` 为解密后的完整密钥, 供管理面板复制使用; 若历史数据未加密存储会为 ``None``.
    """

    id: str = Field(..., description="API Key ID")
    key: str | None = Field(None, description="完整 API Key (解密后)")
    key_prefix: str = Field(..., description="API Key 前缀")
    note: str = Field(..., description="备注")
    created_at: str = Field(..., description="创建时间")
    last_used_at: str | None = Field(None, description="最后使用时间")
    is_active: bool = Field(..., description="是否激活")


class ApiKeyListResponse(BaseModel):
    """API Key 列表响应."""

    items: list[ApiKeyInfo] = Field(..., description="API Key 列表")


class ApiKeyRevokeRequest(BaseModel):
    """撤销 API Key 请求."""

    key_id: str = Field(..., description="API Key ID")
