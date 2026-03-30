"""帐户与认证模块.

包含:
- 管理员用户认证 (User, AuthService)
- API Key 管理 (ApiKey, ApiKeyService)
"""

# 用户认证
from .user_models import User, SessionToken
from .auth_service import (
    AuthService,
    AuthServiceError,
    InvalidCredentialsError,
    UserNotFoundError,
    TokenExpiredError,
    PasswordTooWeakError,
    validate_password_strength,
)
from .sqlite_auth import SqliteAuthService

# API Key
from .api_key_models import ApiKey
from .api_key_store import ApiKeyStore, SqliteApiKeyStore
from .api_key_service import ApiKeyService

__all__ = [
    # 用户认证
    "User",
    "SessionToken",
    "AuthService",
    "AuthServiceError",
    "InvalidCredentialsError",
    "UserNotFoundError",
    "TokenExpiredError",
    "PasswordTooWeakError",
    "SqliteAuthService",
    "validate_password_strength",
    # API Key
    "ApiKey",
    "ApiKeyStore",
    "SqliteApiKeyStore",
    "ApiKeyService",
]
