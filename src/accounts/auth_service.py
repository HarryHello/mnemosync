"""用户认证服务."""

from datetime import datetime, timezone, timedelta
from typing import Protocol

from .user_models import User, SessionToken


class AuthServiceError(Exception):
    """认证服务异常."""
    pass


class InvalidCredentialsError(AuthServiceError):
    """无效凭证错误."""
    pass


class UserNotFoundError(AuthServiceError):
    """用户不存在错误."""
    pass


class TokenExpiredError(AuthServiceError):
    """Token 过期错误."""
    pass


class PasswordTooWeakError(AuthServiceError):
    """密码强度不足错误."""
    pass


class AuthService(Protocol):
    """认证服务协议."""

    async def create_default_user(self, password: str) -> User:
        """创建默认管理员用户."""
        ...

    async def authenticate(self, username: str, password: str) -> User:
        """验证用户凭证."""
        ...

    async def create_session(self, user_id: str) -> SessionToken:
        """创建会话 Token."""
        ...

    async def get_session(self, token: str) -> SessionToken:
        """获取会话."""
        ...

    async def invalidate_session(self, token: str) -> None:
        """使会话失效."""
        ...

    async def change_password(self, user_id: str, old_password: str, new_password: str) -> User:
        """修改密码."""
        ...

    async def get_user_by_id(self, user_id: str) -> User | None:
        """根据 ID 获取用户."""
        ...

    async def get_user_by_username(self, username: str) -> User | None:
        """根据用户名获取用户."""
        ...


def validate_password_strength(password: str, allow_default: bool = False) -> tuple[bool, str | None]:
    """验证密码强度.

    Returns:
        (是否通过，错误信息)
    """
    if len(password) < 6:
        return False, "密码长度至少为 6 位"

    if not allow_default and password == "mnemosync":
        return False, "不能使用默认密码"

    if password.lower() == "password":
        return False, "不能使用弱密码"

    return True, None
