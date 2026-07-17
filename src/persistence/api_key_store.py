"""API Key 存储.

API Key 采用 Fernet 对称加密后落库, 密钥自动生成并存于同库 config 表.
列表接口需要还原完整 key 供管理面板"随时复制", 与 SiliconFlow / DashScope 一致.
"""

from __future__ import annotations

import aiosqlite
import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from cryptography.fernet import Fernet, InvalidToken


API_KEY_SOURCE_USER = "user"
API_KEY_SOURCE_PANEL_DEBUG = "panel-debug"


@dataclass
class ApiKey:
    id: str
    key_hash: str        # sha256(raw_key), 用于鉴权时按 raw_key 反查
    key_prefix: str      # 前 12 字符, 用于快速识别
    note: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    is_active: bool = True
    key_full: str | None = None  # 解密后的完整 key, 仅内存持有
    source: str = API_KEY_SOURCE_USER  # 来源: user (手动) / panel-debug (调试面板自动生成)

    @staticmethod
    def generate(note: str, source: str = API_KEY_SOURCE_USER) -> "ApiKey":
        raw = f"sk-{secrets.token_urlsafe(32)}"
        return ApiKey(
            id=secrets.token_hex(16),
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:12],
            note=note,
            key_full=raw,
            source=source,
        )

    def mark_used(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)


class ApiKeyStore(Protocol):
    async def init_db(self) -> None: ...
    async def save(self, api_key: ApiKey) -> None: ...
    async def get_by_id(self, key_id: str) -> ApiKey | None: ...
    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None: ...
    async def list_all(self, source: str | None = None) -> list[ApiKey]: ...
    async def delete(self, key_id: str) -> bool: ...
    async def update_last_used(self, key_id: str) -> None: ...


class SqliteApiKeyStore:
    """SQLite API Key 存储.

    使用方式:
      * 长连接单例 (推荐, API 层): 应用启动时 ``await store.connect()``, 关闭时 ``await store.close()``.
      * 短连接 (CLI / 一次性脚本 / 测试): 不调 ``connect()``, 每次方法内部临时开连接 (旧行为).
    """

    _ENCRYPTION_KEY_ID = "__api_key_encryption_key__"

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._fernet: Optional[Fernet] = None

    # ============ 生命周期 ============

    async def connect(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._init_schema(self._db)
        await self._db.commit()
        await self._migrate_legacy_plaintext(self._db)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def _conn(self):
        if self._db is not None:
            yield self._db
        else:
            async with aiosqlite.connect(self.db_path) as db:
                yield db

    # ============ 加密 ============

    async def _load_or_create_key(self, db: aiosqlite.Connection) -> bytes:
        async with db.execute(
            "SELECT value FROM api_key_config WHERE key = ?", (self._ENCRYPTION_KEY_ID,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return base64.urlsafe_b64decode(row[0])
        key = Fernet.generate_key()
        await db.execute(
            "INSERT OR REPLACE INTO api_key_config (key, value) VALUES (?, ?)",
            (self._ENCRYPTION_KEY_ID, base64.urlsafe_b64encode(key).decode()),
        )
        await db.commit()
        return key

    async def _get_fernet(self, db: aiosqlite.Connection) -> Fernet:
        if self._fernet is None:
            key = await self._load_or_create_key(db)
            self._fernet = Fernet(key)
        return self._fernet

    async def _encrypt(self, db: aiosqlite.Connection, plaintext: str) -> str:
        f = await self._get_fernet(db)
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    async def _decrypt(self, db: aiosqlite.Connection, ciphertext: str) -> str | None:
        if not ciphertext:
            return None
        f = await self._get_fernet(db)
        try:
            return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return None

    # ============ Schema ============

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_key_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_used_at TIMESTAMP,
                is_active INTEGER NOT NULL DEFAULT 1,
                key_full TEXT,
                key_encrypted TEXT,
                source TEXT NOT NULL DEFAULT 'user'
            )
        """)
        # 兼容早期库
        for ddl in (
            "ALTER TABLE api_keys ADD COLUMN key_full TEXT",
            "ALTER TABLE api_keys ADD COLUMN key_encrypted TEXT",
            "ALTER TABLE api_keys ADD COLUMN source TEXT NOT NULL DEFAULT 'user'",
        ):
            try:
                await db.execute(ddl)
            except aiosqlite.OperationalError:
                pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_is_active ON api_keys(is_active)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_source ON api_keys(source)")

    async def _migrate_legacy_plaintext(self, db: aiosqlite.Connection) -> None:
        """把历史明文 key_full 加密写入 key_encrypted, 然后清空明文列."""
        async with db.execute(
            "SELECT id, key_full FROM api_keys WHERE key_full IS NOT NULL AND key_full != ''"
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return
        for row in rows:
            key_id, plaintext = row[0], row[1]
            encrypted = await self._encrypt(db, plaintext)
            await db.execute(
                "UPDATE api_keys SET key_encrypted = ?, key_full = NULL WHERE id = ?",
                (encrypted, key_id),
            )
        await db.commit()

    async def init_db(self) -> None:
        """兼容旧接口: 幂等初始化 schema, 并迁移旧明文 key_full."""
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()
            await self._migrate_legacy_plaintext(db)

    # ============ CRUD ============

    async def save(self, api_key: ApiKey) -> None:
        if api_key.key_full:
            api_key.key_hash = hashlib.sha256(api_key.key_full.encode()).hexdigest()
        async with self._conn() as db:
            encrypted = (
                await self._encrypt(db, api_key.key_full) if api_key.key_full else None
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO api_keys
                (id, key_hash, key_prefix, note, created_at, last_used_at, is_active, key_full, key_encrypted, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    api_key.id,
                    api_key.key_hash,
                    api_key.key_prefix,
                    api_key.note,
                    api_key.created_at.isoformat(),
                    api_key.last_used_at.isoformat() if api_key.last_used_at else None,
                    1 if api_key.is_active else 0,
                    encrypted,
                    api_key.source or API_KEY_SOURCE_USER,
                ),
            )
            await db.commit()

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        async with self._conn() as db:
            async with db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return await self._row_to_api_key(db, row)

    async def get_by_raw_key(self, raw_key: str) -> ApiKey | None:
        """根据请求中的 raw key 查找活跃的 ApiKey."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
                (key_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return await self._row_to_api_key(db, row)

    async def list_all(self, source: str | None = None) -> list[ApiKey]:
        """列出 API Key. 传入 source 时按来源过滤 (面板视图应传 'user' 只看用户创建的)."""
        async with self._conn() as db:
            if source is None:
                async with db.execute("SELECT * FROM api_keys ORDER BY created_at DESC") as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM api_keys WHERE source = ? ORDER BY created_at DESC",
                    (source,),
                ) as cursor:
                    rows = await cursor.fetchall()
            return [await self._row_to_api_key(db, r) for r in rows]

    async def delete_by_source(self, source: str) -> int:
        """按来源批量删除, 返回删除条数. 用于清理 panel-debug 孤儿 key."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM api_keys WHERE source = ?", (source,))
            await db.commit()
            return cur.rowcount

    async def count_active(self) -> int:
        """活跃 API Key 数 (用于仪表盘聚合)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM api_keys WHERE is_active = 1"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def delete(self, key_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            await db.commit()
            return cur.rowcount > 0

    async def update_last_used(self, key_id: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), key_id),
            )
            await db.commit()

    async def _row_to_api_key(self, db: aiosqlite.Connection, row: tuple) -> ApiKey:
        # 列顺序: id, key_hash, key_prefix, note, created_at, last_used_at, is_active, key_full, key_encrypted, source
        legacy_plain = row[7] if len(row) > 7 else None
        encrypted = row[8] if len(row) > 8 else None
        source = row[9] if len(row) > 9 and row[9] else API_KEY_SOURCE_USER
        key_full: str | None = None
        if encrypted:
            key_full = await self._decrypt(db, encrypted)
        elif legacy_plain:
            key_full = legacy_plain
        return ApiKey(
            id=row[0],
            key_hash=row[1],
            key_prefix=row[2],
            note=row[3],
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            last_used_at=datetime.fromisoformat(row[5]) if row[5] else None,
            is_active=bool(row[6]),
            key_full=key_full,
            source=source,
        )
