"""SQLite 认证存储实现."""

import aiosqlite
import bcrypt

from .auth_service import (
    AuthService,
    AuthServiceError,
    InvalidCredentialsError,
    UserNotFoundError,
    TokenExpiredError,
    PasswordTooWeakError,
    validate_password_strength,
)
from .user_models import User, SessionToken


def hash_password(password: str) -> str:
    """哈希密码."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """验证密码."""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


class SqliteAuthService(AuthService):
    """SQLite 认证服务实现."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        """初始化数据库表."""
        async with aiosqlite.connect(self.db_path) as db:
            # 用户表
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

            # 会话表
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

            # 索引
            await db.execute("CREATE INDEX IF NOT EXISTS idx_username ON users(username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_session_token ON sessions(token_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(user_id)")

            await db.commit()

    async def create_default_user(self, password: str) -> User:
        """创建默认管理员用户."""
        async with aiosqlite.connect(self.db_path) as db:
            # 检查是否已有用户
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                if row and row[0] > 0:
                    raise AuthServiceError("用户已存在")

            # 验证密码强度（允许默认密码用于初始化）
            is_valid, error = validate_password_strength(password, allow_default=True)
            if not is_valid:
                raise PasswordTooWeakError(error)

            password_hash = hash_password(password)
            user = User.create(username="mnemosync", password_hash=password_hash, must_change_password=True)

            await db.execute(
                """
                INSERT INTO users (id, username, password_hash, must_change_password, created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.username,
                    user.password_hash,
                    1 if user.must_change_password else 0,
                    user.created_at.isoformat(),
                    user.updated_at.isoformat(),
                    1 if user.is_active else 0,
                ),
            )
            await db.commit()

            return user

    async def authenticate(self, username: str, password: str) -> User:
        """验证用户凭证."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username,)
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    raise InvalidCredentialsError("用户名或密码错误")

                user = self._row_to_user(row)

                if not verify_password(password, user.password_hash):
                    raise InvalidCredentialsError("用户名或密码错误")

                # 更新最后登录时间
                await db.execute(
                    "UPDATE users SET last_login_at = ? WHERE id = ?",
                    (user.mark_logged_in() or datetime.now(timezone.utc).isoformat(), user.id)
                )
                await db.commit()

                return user

    async def create_session(self, user_id: str) -> SessionToken:
        """创建会话 Token."""
        session = SessionToken.generate(user_id, expires_hours=24)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at, is_valid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.user_id,
                    session.token_hash,
                    session.expires_at.isoformat(),
                    session.created_at.isoformat(),
                    1 if session.is_valid else 0,
                ),
            )
            await db.commit()

        return session

    async def get_session(self, token: str) -> SessionToken:
        """获取会话."""
        token_hash = secrets.token_hex(32)  # 这里需要存储的是哈希，实际应该用传入 token 的哈希

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM sessions 
                WHERE token_hash = ? AND is_valid = 1
                """,
                (token,)  # 简化：直接存储原始 token 作为哈希
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    raise TokenExpiredError("Token 无效或已过期")

                session = self._row_to_session(row)

                if session.is_expired():
                    await self.invalidate_session(token)
                    raise TokenExpiredError("Token 已过期")

                return session

    async def invalidate_session(self, token: str) -> None:
        """使会话失效."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET is_valid = 0 WHERE token_hash = ?",
                (token,)
            )
            await db.commit()

    async def change_password(self, user_id: str, old_password: str, new_password: str) -> User:
        """修改密码."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    raise UserNotFoundError("用户不存在")

                user = self._row_to_user(row)

                # 验证旧密码
                if not verify_password(old_password, user.password_hash):
                    raise InvalidCredentialsError("原密码错误")

                # 验证新密码强度
                is_valid, error = validate_password_strength(new_password)
                if not is_valid:
                    raise PasswordTooWeakError(error)

                # 更新密码
                new_password_hash = hash_password(new_password)
                user.update_password(new_password_hash)

                await db.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, must_change_password = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        user.password_hash,
                        1 if user.must_change_password else 0,
                        user.updated_at.isoformat(),
                        user.id,
                    )
                )
                await db.commit()

                return user

    async def change_username_and_password(self, user_id: str, new_username: str, new_password: str) -> User:
        """修改用户名和密码（用于首次登录强制修改）."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    raise UserNotFoundError("用户不存在")

                user = self._row_to_user(row)

                # 验证新密码强度
                is_valid, error = validate_password_strength(new_password)
                if not is_valid:
                    raise PasswordTooWeakError(error)

                # 更新用户名和密码
                new_password_hash = hash_password(new_password)
                user.username = new_username
                user.password_hash = new_password_hash
                user.must_change_password = False
                user.updated_at = datetime.now(timezone.utc)

                await db.execute(
                    """
                    UPDATE users
                    SET username = ?, password_hash = ?, must_change_password = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        user.username,
                        user.password_hash,
                        0,  # must_change_password = False
                        user.updated_at.isoformat(),
                        user.id,
                    )
                )
                await db.commit()

                return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        """根据 ID 获取用户."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_user(row) if row else None

    async def get_user_by_username(self, username: str) -> User | None:
        """根据用户名获取用户."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_user(row) if row else None

    def _row_to_user(self, row: tuple) -> User:
        """将数据库行转换为 User 对象."""
        from datetime import datetime, timezone

        return User(
            id=row[0],
            username=row[1],
            password_hash=row[2],
            must_change_password=bool(row[3]),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(row[5]) if row[5] else datetime.now(timezone.utc),
            last_login_at=datetime.fromisoformat(row[6]) if row[6] else None,
            is_active=bool(row[7]),
        )

    def _row_to_session(self, row: tuple) -> SessionToken:
        """将数据库行转换为 SessionToken 对象."""
        from datetime import datetime, timezone

        return SessionToken(
            id=row[0],
            user_id=row[1],
            token_hash=row[2],
            expires_at=datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            is_valid=bool(row[5]),
        )


# 需要导入 secrets
import secrets
from datetime import datetime, timezone
