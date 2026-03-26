"""API Schemas."""

from .api_key import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyListResponse,
    ApiKeyRevokeRequest,
)
from .auth import (
    LoginRequest,
    LoginResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    UserInfo,
    UserInfoResponse,
    LogoutRequest,
    MessageResponse,
)

__all__ = [
    # API Key
    "ApiKeyCreateRequest",
    "ApiKeyCreateResponse",
    "ApiKeyInfo",
    "ApiKeyListResponse",
    "ApiKeyRevokeRequest",
    # Auth
    "LoginRequest",
    "LoginResponse",
    "ChangePasswordRequest",
    "ChangePasswordResponse",
    "UserInfo",
    "UserInfoResponse",
    "LogoutRequest",
    "MessageResponse",
]
