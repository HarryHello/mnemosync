"""记忆存储接口."""

import aiosqlite
from typing import Protocol
from datetime import datetime, timezone

from .models import MemoryEntry, Visibility


class MemoryStore(Protocol):
    """记忆存储协议."""

    async def save(self, entry: MemoryEntry) -> None:
        """保存记忆条目."""
        ...

    async def get_by_id(self, entry_id: str) -> MemoryEntry | None:
        """根据 ID 获取记忆."""
        ...

    async def query(
        self,
        source_user: str | None = None,
        visibility: list[Visibility] | None = None,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[MemoryEntry]:
        """查询记忆."""
        ...

    async def delete(self, entry_id: str) -> bool:
        """删除记忆."""
        ...

    async def init_db(self) -> None:
        """初始化数据库."""
        ...


class SqliteMemoryStore:
    """SQLite 记忆存储实现."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        """初始化数据库表."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source_user TEXT,
                    visibility TEXT NOT NULL DEFAULT 'source_restricted',
                    custom_policies TEXT,
                    emotional_tags TEXT,
                    created_at TIMESTAMP NOT NULL,
                    last_accessed TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """)

            # 索引优化
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_user 
                ON memory_entries(source_user)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_visibility 
                ON memory_entries(visibility)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at 
                ON memory_entries(created_at DESC)
            """)

            await db.commit()

    async def save(self, entry: MemoryEntry) -> None:
        """保存记忆条目."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO memory_entries
                (id, content, role, source_user, visibility, custom_policies,
                 emotional_tags, created_at, last_accessed, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.content,
                    entry.role,
                    entry.source_user,
                    entry.visibility.value,
                    ",".join(entry.custom_policies) if entry.custom_policies else None,
                    ",".join(entry.emotional_tags) if entry.emotional_tags else None,
                    entry.created_at.isoformat(),
                    entry.last_accessed.isoformat() if entry.last_accessed else None,
                    entry.expires_at.isoformat() if entry.expires_at else None,
                ),
            )
            await db.commit()

    async def get_by_id(self, entry_id: str) -> MemoryEntry | None:
        """根据 ID 获取记忆."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_entry(row)
                return None

    async def query(
        self,
        source_user: str | None = None,
        visibility: list[Visibility] | None = None,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[MemoryEntry]:
        """查询记忆.

        Args:
            source_user: 来源用户过滤
            visibility: 可见性过滤
            limit: 返回数量限制
            before: 只返回此时间之前的记忆

        Returns:
            记忆条目列表，按创建时间倒序
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 构建查询
            query = "SELECT * FROM memory_entries WHERE 1=1"
            params = []

            if source_user:
                query += " AND source_user = ?"
                params.append(source_user)

            if visibility:
                placeholders = ",".join("?" for _ in visibility)
                query += f" AND visibility IN ({placeholders})"
                params.extend(v.value for v in visibility)

            if before:
                query += " AND created_at < ?"
                params.append(before.isoformat())

            # 排除过期记忆
            query += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(datetime.now(timezone.utc).isoformat())

            # 按时间倒序，限制数量
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]

    async def delete(self, entry_id: str) -> bool:
        """删除记忆."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    def _row_to_entry(self, row: tuple) -> MemoryEntry:
        """将数据库行转换为记忆条目."""
        return MemoryEntry(
            id=row[0],
            content=row[1],
            role=row[2],
            source_user=row[3],
            visibility=Visibility(row[4]),
            custom_policies=row[5].split(",") if row[5] else [],
            emotional_tags=row[6].split(",") if row[6] else [],
            created_at=self._parse_datetime(row[7]),
            last_accessed=self._parse_datetime(row[8]),
            expires_at=self._parse_datetime(row[9]),
        )

    def _parse_datetime(self, value: str | None) -> datetime | None:
        """解析时间戳."""
        if value is None:
            return None
        return datetime.fromisoformat(value)
