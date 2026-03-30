"""API Key 存储层."""

import aiosqlite
from typing import Protocol

from .api_key_models import ApiKey


class ApiKeyStore(Protocol):
    """API Key 存储协议."""

    async def save(self, api_key: ApiKey) -> None:
        """保存 API Key."""
        ...

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        """根据 ID 获取 API Key."""
        ...

    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None:
        """根据密钥哈希获取 API Key."""
        ...

    async def list_all(self) -> list[ApiKey]:
        """列出所有 API Key."""
        ...

    async def delete(self, key_id: str) -> bool:
        """删除 API Key."""
        ...

    async def update_last_used(self, key_id: str) -> None:
        """更新最后使用时间."""
        ...

    async def init_db(self) -> None:
        """初始化数据库表."""
        ...


class SqliteApiKeyStore:
    """SQLite API Key 存储实现."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        """初始化数据库表."""
        async with aiosqlite.connect(self.db_path) as db:
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
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_is_active ON api_keys(is_active)
            """)
            await db.commit()

    async def save(self, api_key: ApiKey) -> None:
        """保存 API Key."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO api_keys
                (id, key_hash, key_prefix, note, created_at, last_used_at, is_active, key_full)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    api_key.id,
                    api_key.key_hash,
                    api_key.key_prefix,
                    api_key.note,
                    api_key.created_at.isoformat(),
                    api_key.last_used_at.isoformat() if api_key.last_used_at else None,
                    1 if api_key.is_active else 0,
                    api_key.key_full,
                ),
            )
            await db.commit()

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        """根据 ID 获取 API Key."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_api_key(row)
                return None

    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None:
        """根据密钥哈希获取 API Key."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
                (key_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_api_key(row)
                return None

    async def list_all(self) -> list[ApiKey]:
        """列出所有 API Key."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM api_keys ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_api_key(row) for row in rows]

    async def delete(self, key_id: str) -> bool:
        """删除 API Key."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM api_keys WHERE id = ?", (key_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_last_used(self, key_id: str) -> None:
        """更新最后使用时间."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), key_id),
            )
            await db.commit()

    @staticmethod
    def _row_to_api_key(row: tuple) -> ApiKey:
        """将数据库行转换为 ApiKey 对象."""
        from datetime import datetime, timezone

        return ApiKey(
            id=row[0],
            key_hash=row[1],
            key_prefix=row[2],
            note=row[3],
            created_at=datetime.fromisoformat(row[4])
            if row[4]
            else datetime.now(timezone.utc),
            last_used_at=datetime.fromisoformat(row[5]) if row[5] else None,
            is_active=bool(row[6]),
            key_full=row[7] if len(row) > 7 else None,
        )
