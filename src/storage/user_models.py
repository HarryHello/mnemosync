"""用户数据模型."""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


@dataclass
class User:
    """用户数据模型."""

    id: str
    username: str
    password_hash: str
    must_change_password: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = None
    is_active: bool = True

    @staticmethod
    def create(username: str, password_hash: str, must_change_password: bool = True) -> "User":
        """创建新用户."""
        now = datetime.now(timezone.utc)
        return User(
            id=secrets.token_hex(16),
            username=username,
            password_hash=password_hash,
            must_change_password=must_change_password,
            created_at=now,
            updated_at=now,
        )

    def update_password(self, new_password_hash: str) -> None:
        """更新密码."""
        self.password_hash = new_password_hash
        self.must_change_password = False
        self.updated_at = datetime.now(timezone.utc)

    def mark_logged_in(self) -> None:
        """标记已登录."""
        self.last_login_at = datetime.now(timezone.utc)


@dataclass
class SessionToken:
    """会话 Token 模型."""

    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_valid: bool = True

    @staticmethod
    def generate(user_id: str, expires_hours: int = 24) -> "SessionToken":
        """生成新的会话 Token."""
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        return SessionToken(
            id=secrets.token_hex(16),
            user_id=user_id,
            token_hash=secrets.token_hex(32),  # 存储哈希值
            expires_at=now + timedelta(hours=expires_hours),
        )

    def is_expired(self) -> bool:
        """检查是否过期."""
        return datetime.now(timezone.utc) > self.expires_at

    def invalidate(self) -> None:
        """使 Token 失效."""
        self.is_valid = False
