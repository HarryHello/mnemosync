"""管理员认证存储.

复用旧 accounts/sqlite_auth.py 的核心逻辑（bcrypt + session token）,
适配新目录结构.

v0.3.2: Session token 使用 HMAC-SHA256 签名. 数据库仅存储随机 token 的
HMAC 摘要; 验证时需要服务器密钥 (从 MNEMOSYNC_SESSION_KEY 环境变量或
自动生成的本地密钥派生), 数据库泄漏后攻击者无法离线伪造有效令牌.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Protocol

import aiosqlite
import bcrypt

from src.persistence.base import SqliteStore

# 会话签名密钥路径 (data/.session_key); 启动时若不存在则自动生成
_SESSION_KEY_PATH = Path("data/.session_key")


def _load_or_create_session_key() -> bytes:
    """加载或创建 HMAC 签名密钥.

    优先读取 MNEMOSYNC_SESSION_KEY 环境变量 (hex 或 base64 编码),
    否则从 data/.session_key 文件加载, 文件不存在则自动生成 32 字节
    随机密钥并持久化 (跨重启保持已有会话有效).
    """
    env_key = os.environ.get("MNEMOSYNC_SESSION_KEY")
    if env_key:
        import base64
        try:
            return bytes.fromhex(env_key)
        except ValueError:
            return base64.b64decode(env_key)

    _SESSION_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _SESSION_KEY_PATH.exists():
        return _SESSION_KEY_PATH.read_bytes()
    key = secrets.token_bytes(32)
    _SESSION_KEY_PATH.write_bytes(key)
    try:
        os.chmod(_SESSION_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def _sign_token(raw_token: str, key: bytes | None = None) -> str:
    """对 token 计算 HMAC-SHA256 签名 (hex)."""
    if key is None:
        key = _load_or_create_session_key()
    return hmac.new(key, raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def _hash_token(raw_token: str) -> str:
    """计算 token 的存储摘要: HMAC-SHA256 签名 (替代裸 SHA-256)."""
    return _sign_token(raw_token)


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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_login_at: datetime | None = None
    is_active: bool = True

    @staticmethod
    def create(username: str, password_hash: str, must_change_password: bool = True) -> User:
        now = datetime.now(UTC)
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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_valid: bool = True

    @staticmethod
    def generate(user_id: str, expires_hours: int = 24) -> SessionToken:
        raw = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw)
        return SessionToken(
            id=secrets.token_hex(16),
            user_id=user_id,
            token_hash=token_hash,
            raw_token=raw,
            expires_at=datetime.now(UTC) + timedelta(hours=expires_hours),
        )

    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


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


class SqliteAuthStore(SqliteStore):
    """SQLite 认证存储实现.

    使用方式:
      * 长连接单例 (推荐, API 层): 应用启动时 ``await store.connect()``, 关闭时 ``await store.close()``.
        所有方法共用同一条 aiosqlite 连接, 无每请求 open/close 开销.
      * 短连接 (CLI / 一次性脚本): 不调 ``connect()``, 每次方法内部临时开连接 (旧行为).
    """

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
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

    async def init_db(self) -> None:
        """兼容旧接口: 幂等地初始化 schema. 长连接模式下 connect() 已包含此步骤."""
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def has_any_user(self) -> bool:
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self._conn() as db:
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
        async with self._conn() as db:
            await db.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), user.id),
            )
            await db.commit()
        return user

    async def create_session(self, user_id: str) -> SessionToken:
        session = SessionToken.generate(user_id)
        async with self._conn() as db:
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
        """根据 raw_token 查询会话.

        token_hash 字段存储 HMAC-SHA256(server_secret, raw_token);
        验证时用相同密钥计算 HMAC 比对, 防止数据库泄漏后离线伪造令牌.
        """
        token_hash = _hash_token(token)
        async with self._conn() as db:
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
        token_hash = _hash_token(token)
        async with self._conn() as db:
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
        user.updated_at = datetime.now(UTC)
        async with self._conn() as db:
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

    async def change_username_and_password(
        self, user_id: str, old_password: str, new_username: str, new_password: str
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")
        if not verify_password(old_password, user.password_hash):
            raise ValueError("原密码错误")

        existing = await self.get_user_by_username(new_username)
        if existing and existing.id != user_id:
            raise ValueError("用户名已被占用")

        ok, err = validate_password_strength(new_password)
        if not ok:
            raise ValueError(err or "密码强度不足")

        user.username = new_username
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.updated_at = datetime.now(UTC)

        async with self._conn() as db:
            await db.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, must_change_password = ?, updated_at = ?
                WHERE id = ?
                """,
                (user.username, user.password_hash, 0, user.updated_at.isoformat(), user.id),
            )
            await db.commit()
        return user

    def _row_to_user(self, row: Sequence[Any]) -> User:
        return User(
            id=row[0], username=row[1], password_hash=row[2],
            must_change_password=bool(row[3]),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(UTC),
            updated_at=datetime.fromisoformat(row[5]) if row[5] else datetime.now(UTC),
            last_login_at=datetime.fromisoformat(row[6]) if row[6] else None,
            is_active=bool(row[7]),
        )

    def _row_to_session(self, row: Sequence[Any], raw_token: str) -> SessionToken:
        return SessionToken(
            id=row[0], user_id=row[1], token_hash=row[2], raw_token=raw_token,
            expires_at=datetime.fromisoformat(row[3]) if row[3] else datetime.now(UTC),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(UTC),
            is_valid=bool(row[5]),
        )
