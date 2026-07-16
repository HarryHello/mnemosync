"""API Key 存储."""

from __future__ import annotations

import aiosqlite
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class ApiKey:
    id: str
    key_hash: str        # 用于验证的哈希（独立随机值，非对 raw_key 做 hash）
    key_prefix: str      # 前 12 字符用于展示
    note: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    is_active: bool = True
    key_full: str | None = None  # 仅生成时持有（不入库，但保留用于显示一次）

    @staticmethod
    def generate(note: str) -> "ApiKey":
        raw = f"sk-{secrets.token_urlsafe(32)}"
        return ApiKey(
            id=secrets.token_hex(16),
            key_hash=secrets.token_hex(32),
            key_prefix=raw[:12],
            note=note,
            key_full=raw,
        )

    def mark_used(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)


class ApiKeyStore(Protocol):
    async def init_db(self) -> None: ...
    async def save(self, api_key: ApiKey) -> None: ...
    async def get_by_id(self, key_id: str) -> ApiKey | None: ...
    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None: ...
    async def list_all(self) -> list[ApiKey]: ...
    async def delete(self, key_id: str) -> bool: ...
    async def update_last_used(self, key_id: str) -> None: ...


class SqliteApiKeyStore:
    """SQLite API Key 存储.

    使用方式:
      * 长连接单例 (推荐, API 层): 应用启动时 ``await store.connect()``, 关闭时 ``await store.close()``.
      * 短连接 (CLI / 一次性脚本 / 测试): 不调 ``connect()``, 每次方法内部临时开连接 (旧行为).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._init_schema(self._db)
        await self._db.commit()

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

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_used_at TIMESTAMP,
                is_active INTEGER NOT NULL DEFAULT 1,
                key_full TEXT
            )
        """)
        # 兼容早期没有 key_full 列的库
        try:
            await db.execute("ALTER TABLE api_keys ADD COLUMN key_full TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_is_active ON api_keys(is_active)")

    async def init_db(self) -> None:
        """兼容旧接口: 幂等地初始化 schema. 长连接模式下 connect() 已包含此步骤."""
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def save(self, api_key: ApiKey) -> None:
        # 重新计算 key_hash 为 raw_key 的 sha256，便于鉴权时按 raw_key 查找
        import hashlib
        actual_hash = hashlib.sha256(api_key.key_full.encode()).hexdigest() if api_key.key_full else api_key.key_hash
        api_key.key_hash = actual_hash
        async with self._conn() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO api_keys
                (id, key_hash, key_prefix, note, created_at, last_used_at, is_active, key_full)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (api_key.id, api_key.key_hash, api_key.key_prefix, api_key.note,
                 api_key.created_at.isoformat(),
                 api_key.last_used_at.isoformat() if api_key.last_used_at else None,
                 1 if api_key.is_active else 0,
                 api_key.key_full),
            )
            await db.commit()

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        async with self._conn() as db:
            async with db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_api_key(row) if row else None

    async def get_by_raw_key(self, raw_key: str) -> ApiKey | None:
        """根据请求中的 raw key 查找活跃的 ApiKey."""
        import hashlib
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
                (key_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_api_key(row) if row else None

    async def list_all(self) -> list[ApiKey]:
        async with self._conn() as db:
            async with db.execute("SELECT * FROM api_keys ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_api_key(r) for r in rows]

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

    def _row_to_api_key(self, row: tuple) -> ApiKey:
        return ApiKey(
            id=row[0], key_hash=row[1], key_prefix=row[2], note=row[3],
            created_at=datetime.fromisoformat(row[4]) if row[4] else datetime.now(timezone.utc),
            last_used_at=datetime.fromisoformat(row[5]) if row[5] else None,
            is_active=bool(row[6]),
            key_full=row[7] if len(row) > 7 else None,
        )
