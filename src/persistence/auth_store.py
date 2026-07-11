"""管理员认证存储.

复用旧 accounts/sqlite_auth.py 的核心逻辑（bcrypt + session token）,
适配新目录结构.
"""

from __future__ import annotations

import aiosqlite
import bcrypt
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_strength(password: str, allow_default: bool = False) -> tuple[bool, str | None]:
    """校验密码强度."""
    if len(password) < 6:
        return False, "密码至少 6 个字符"
    if password == "mnemosync" and not allow_default:
        return False, "不能使用默认密码"
    return True, None


@dataclass
class User:
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
        now = datetime.now(timezone.utc)
        return User(
            id=secrets.token_hex(16),
            username=username,
            password_hash=password_hash,
            must_change_password=must_change_password,
            created_at=now,
            updated_at=now,
        )


@dataclass
class SessionToken:
    id: str
    user_id: str
    token_hash: str
    raw_token: str  # 仅生成时持有，不入库
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_valid: bool = True

    @staticmethod
    def generate(user_id: str, expires_hours: int = 24) -> "SessionToken":
        raw = secrets.token_urlsafe(32)
        import hashlib
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        return SessionToken(
            id=secrets.token_hex(16),
            user_id=user_id,
            token_hash=token_hash,
            raw_token=raw,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_hours),
        )

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class AuthStore(Protocol):
    async def init_db(self) -> None: ...
    async def create_default_user(self, password: str) -> User: ...
    async def authenticate(self, username: str, password: str) -> User: ...
    async def create_session(self, user_id: str) -> SessionToken: ...
    async def get_session(self, token: str) -> SessionToken: ...
    async def invalidate_session(self, token: str) -> None: ...
    async def change_password(self, user_id: str, old_password: str, new_password: str) -> User: ...
    async def get_user_by_id(self, user_id: str) -> User | None: ...
    async def get_user_by_username(self, username: str) -> User | None: ...
    async def has_any_user(self) -> bool: ...


class SqliteAuthStore:
    """SQLite 认证存储实现."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    last_login_at TIMESTAMP,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    is_valid INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_username ON users(username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_session_token ON sessions(token_hash)")
            await db.commit()

    async def has_any_user(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                return bool(row and row[0] > 0)

    async def create_default_user(self, password: str) -> User:
        if await self.has_any_user():
            raise ValueError("用户已存在")
        ok, err = validate_password_strength(password, allow_default=True)
        if not ok:
            raise ValueError(err or "密码强度不足")
        user = User.create("mnemosync", hash_password(password), must_change_password=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (id, username, password_hash, must_change_password,
                                   created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user.id, user.username, user.password_hash,
                 1, user.created_at.isoformat(), user.updated_at.isoformat(), 1),
            )
            await db.commit()
        return user

    async def get_user_by_username(self, username: str) -> User | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_user(row) if row else None

    async def authenticate(self, username: str, password: str) -> User:
        user = await self.get_user_by_username(username)
        if not user or not user.is_active:
            raise ValueError("用户名或密码错误")
        if not verify_password(password, user.password_hash):
            raise ValueError("用户名或密码错误")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), user.id),
            )
            await db.commit()
        return user

    async def create_session(self, user_id: str) -> SessionToken:
        session = SessionToken.generate(user_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at, is_valid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session.id, session.user_id, session.token_hash,
                 session.expires_at.isoformat(), session.created_at.isoformat(), 1),
            )
            await db.commit()
        return session

    async def get_session(self, token: str) -> SessionToken:
        """根据 raw_token 查询会话（raw_token 本身即作为查询 key，简化）."""
        # 注意: 生产环境应 hash token 后比对。此处用 token_hash 字段存储 raw 的 sha256。
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND is_valid = 1",
                (token_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise ValueError("Token 无效")
                session = self._row_to_session(row, token)
                if session.is_expired():
                    await self.invalidate_session(token)
                    raise ValueError("Token 已过期")
                return session

    async def invalidate_session(self, token: str) -> None:
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET is_valid = 0 WHERE token_hash = ?", (token_hash,)
            )
            await db.commit()

    async def change_password(
        self, user_id: str, old_password: str, new_password: str
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")
        if not verify_password(old_password, user.password_hash):
            raise ValueError("原密码错误")
        ok, err = validate_password_strength(new_password)
        if not ok:
            raise ValueError(err or "密码强度不足")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.updated_at = datetime.now(timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE users
                SET password_hash = ?, must_change_password = ?, updated_at = ?
                WHERE id = ?
                """,
                (user.password_hash, 0, user.updated_at.isoformat(), user.id),
            )
            await db.commit()
        return user

    def _row_to_user(self, row: tuple) -> User:
        return User(
            id=row[0], username=row[1], password_hash=row[2],
            must_change_password=bool(row[3]),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(row[5]) if row[5] else datetime.now(timezone.utc),
            last_login_at=datetime.fromisoformat(row[6]) if row[6] else None,
            is_active=bool(row[7]),
        )

    def _row_to_session(self, row: tuple, raw_token: str) -> SessionToken:
        return SessionToken(
            id=row[0], user_id=row[1], token_hash=row[2], raw_token=raw_token,
            expires_at=datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            is_valid=bool(row[5]),
        )
