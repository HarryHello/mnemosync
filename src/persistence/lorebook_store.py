"""Lorebook 条目存储.

Lorebook 是作者预定义的固定知识 (世界观、设定、固定关系),
与对话中提取的长期记忆分离。条目通过关键词匹配 + 语义检索触发,
生命周期跟随人格版本, 不衰减不遗忘。
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.persistence.base import SqliteStore, resolve_sort_params

logger = logging.getLogger(__name__)


@dataclass
class LorebookEntry:
    """一条 Lorebook 条目."""

    id: str
    content: str
    keywords: list[str]
    priority: int = 0
    space_id: str | None = None
    persona_version_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @staticmethod
    def create(
        content: str,
        keywords: list[str],
        *,
        priority: int = 0,
        space_id: str | None = None,
    ) -> LorebookEntry:
        return LorebookEntry(
            id=f"lb_{secrets.token_hex(12)}",
            content=content,
            keywords=keywords,
            priority=priority,
            space_id=space_id,
        )


class SqliteLorebookStore(SqliteStore):
    """SQLite Lorebook 条目存储."""

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lorebook_entries (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                keywords TEXT NOT NULL DEFAULT '[]',
                priority INTEGER DEFAULT 0,
                space_id TEXT,
                persona_version_id INTEGER,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_lorebook_space "
            "ON lorebook_entries(space_id)"
        )

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def save(self, entry: LorebookEntry) -> None:
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO lorebook_entries "
                "(id, content, keywords, priority, space_id, persona_version_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.content,
                    json.dumps(entry.keywords, ensure_ascii=False),
                    entry.priority,
                    entry.space_id,
                    entry.persona_version_id,
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                ),
            )
            await db.commit()

    async def get_by_id(self, entry_id: str) -> LorebookEntry | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM lorebook_entries WHERE id = ?", (entry_id,)
            ) as cur:
                row = await cur.fetchone()
                return self._row_to_entry(row) if row else None

    async def delete(self, entry_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM lorebook_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        space_id: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[LorebookEntry], int]:
        sort_col, direction = resolve_sort_params(
            sort_by, sort_order,
            {
                "created_at": "created_at",
                "priority": "priority",
                "content": "content",
            },
            default_col="created_at",
        )
        where = []
        params = []
        if space_id is not None:
            where.append("space_id = ?")
            params.append(space_id)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM lorebook_entries{where_sql}", tuple(params)
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                f"SELECT * FROM lorebook_entries{where_sql} "
                f"ORDER BY {sort_col} {direction}, id ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_entry(r) for r in rows], total

    async def match_for_space(
        self,
        text: str,
        space_id: str | None = None,
        *,
        limit: int = 5,
    ) -> list[LorebookEntry]:
        """关键词匹配: 返回当前空间命中的 Lorebook 条目, 按优先级排序."""
        if not text:
            return []
        where = []
        params = []
        if space_id:
            where.append("(space_id = ? OR space_id IS NULL)")
            params.append(space_id)
        else:
            where.append("space_id IS NULL")
        where_sql = " AND ".join(where)
        async with self._conn() as db:
            async with db.execute(
                f"SELECT * FROM lorebook_entries WHERE {where_sql} "
                f"ORDER BY priority DESC LIMIT ?",
                (*params, limit * 3),
            ) as cur:
                rows = await cur.fetchall()
        candidates = [self._row_to_entry(r) for r in rows]
        # 在 Python 侧做关键词匹配 (支持中文分词)
        matched: list[LorebookEntry] = []
        for entry in candidates:
            for kw in entry.keywords:
                if kw and kw.lower() in text.lower():
                    matched.append(entry)
                    break
            if len(matched) >= limit:
                break
        matched.sort(key=lambda e: e.priority, reverse=True)
        return matched[:limit]

    async def count(self) -> int:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM lorebook_entries") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    @staticmethod
    def _row_to_entry(row: Sequence[Any]) -> LorebookEntry:
        try:
            keywords = json.loads(row[2]) if isinstance(row[2], str) else []
        except (json.JSONDecodeError, TypeError):
            keywords = []
        return LorebookEntry(
            id=row[0],
            content=row[1],
            keywords=keywords,
            priority=row[3],
            space_id=row[4],
            persona_version_id=row[5],
            created_at=datetime.fromisoformat(row[6]) if row[6] else datetime.now(UTC),
            updated_at=datetime.fromisoformat(row[7]) if row[7] else datetime.now(UTC),
        )


