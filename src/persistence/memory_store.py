"""记忆持久化存储.

SQLite 存储记忆元数据 + 关系状态.
向量数据由 infra/vector_store.py 存入 ChromaDB.
"""

from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone
from typing import Protocol

from src.core.memory.models import (
    DecayState,
    MemoryEntry,
    MemoryType,
    Relationship,
    Visibility,
)


def _dt(v: datetime | None) -> str | None:
    """datetime → ISO 字符串."""
    return v.isoformat() if v else None


def _parse_dt(v: str | None) -> datetime | None:
    """ISO 字符串 → datetime."""
    if not v:
        return None
    return datetime.fromisoformat(v)


class MemoryStore(Protocol):
    """记忆存储协议."""

    async def init_db(self) -> None: ...
    async def save(self, entry: MemoryEntry) -> None: ...
    async def get_by_id(self, entry_id: str) -> MemoryEntry | None: ...
    async def delete(self, entry_id: str) -> bool: ...
    async def list_permanent(self, source_user: str, limit: int = 7) -> list[MemoryEntry]: ...
    async def list_for_decay(self, skip_hours: int = 24, limit: int = 50) -> list[MemoryEntry]: ...
    async def update_priority(self, entry_id: str, priority: float, is_forgotten: bool) -> None: ...
    async def mark_accessed(self, entry_id: str) -> None: ...
    async def count_permanent(self, source_user: str) -> int: ...


class SqliteMemoryStore:
    """SQLite 记忆存储实现.

    表结构:
        memory_entries — 完整记忆字段
        relationships  — 用户关系状态
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source_user TEXT,
                    memory_type TEXT NOT NULL DEFAULT 'normal',
                    importance REAL NOT NULL DEFAULT 0.5,
                    decay_rate REAL NOT NULL DEFAULT 0.3,
                    priority REAL NOT NULL DEFAULT 0.5,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    is_forgotten INTEGER NOT NULL DEFAULT 0,
                    visibility TEXT NOT NULL DEFAULT 'source_restricted',
                    custom_policies TEXT,
                    emotional_tags TEXT,
                    related_memories TEXT,
                    created_at TIMESTAMP NOT NULL,
                    last_accessed TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_user ON memory_entries(source_user)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(memory_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_priority ON memory_entries(priority DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_is_forgotten ON memory_entries(is_forgotten)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON memory_entries(created_at DESC)"
            )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    persona_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'stranger',
                    intimacy_score REAL NOT NULL DEFAULT 0.0,
                    trust_level REAL NOT NULL DEFAULT 0.0,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    last_active TIMESTAMP,
                    notes TEXT,
                    PRIMARY KEY (persona_id, user_id)
                )
            """)
            await db.commit()

    # ============ MemoryEntry CRUD ============

    async def save(self, entry: MemoryEntry) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO memory_entries
                (id, content, role, source_user, memory_type, importance, decay_rate,
                 priority, access_count, is_forgotten, visibility, custom_policies,
                 emotional_tags, related_memories, created_at, last_accessed, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.content,
                    entry.role,
                    entry.source_user,
                    entry.memory_type.value,
                    entry.importance,
                    entry.decay_rate,
                    entry.priority,
                    entry.access_count,
                    1 if entry.is_forgotten else 0,
                    entry.visibility.value,
                    "|".join(entry.custom_policies) if entry.custom_policies else None,
                    "|".join(entry.emotional_tags) if entry.emotional_tags else None,
                    "|".join(entry.related_memories) if entry.related_memories else None,
                    _dt(entry.created_at),
                    _dt(entry.last_accessed),
                    _dt(entry.expires_at),
                ),
            )
            await db.commit()

    async def get_by_id(self, entry_id: str) -> MemoryEntry | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_entry(row) if row else None

    async def delete(self, entry_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_permanent(self, source_user: str, limit: int = 7) -> list[MemoryEntry]:
        """加载用户的永久记忆（按重要性降序）."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM memory_entries
                WHERE source_user = ? AND memory_type = 'permanent' AND is_forgotten = 0
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (source_user, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    async def list_active_normal(
        self, source_user: str, limit: int = 5
    ) -> list[MemoryEntry]:
        """加载用户 ACTIVE 状态的普通记忆（按优先级降序）.

        用于主对话 Agent 上下文拼装（当无语义检索时）.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM memory_entries
                WHERE source_user = ? AND memory_type = 'normal' AND is_forgotten = 0
                  AND priority > 0.3
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (source_user, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    async def list_for_decay(self, skip_hours: int = 24, limit: int = 50) -> list[MemoryEntry]:
        """列出待衰减评估的普通记忆（跳过 skip_hours 小时内新建的）."""
        from datetime import timedelta

        threshold = (datetime.now(timezone.utc) - timedelta(hours=skip_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM memory_entries
                WHERE memory_type = 'normal' AND is_forgotten = 0
                  AND created_at < ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (threshold, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    async def update_priority(
        self, entry_id: str, priority: float, is_forgotten: bool
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE memory_entries
                SET priority = ?, is_forgotten = ?
                WHERE id = ?
                """,
                (priority, 1 if is_forgotten else 0, entry_id),
            )
            await db.commit()

    async def mark_accessed(self, entry_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE memory_entries
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id = ?
                """,
                (now, entry_id),
            )
            await db.commit()

    async def count_permanent(self, source_user: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM memory_entries
                WHERE source_user = ? AND memory_type = 'permanent'
                """,
                (source_user,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def list_all_for_user(self, source_user: str, limit: int = 20) -> list[MemoryEntry]:
        """列出用户的所有未遗忘记忆（调试用）."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM memory_entries
                WHERE source_user = ? AND is_forgotten = 0
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (source_user, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    # ============ Relationship CRUD ============

    async def get_relationship(self, persona_id: str, user_id: str) -> Relationship | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM relationships WHERE persona_id = ? AND user_id = ?
                """,
                (persona_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_relationship(row) if row else None

    async def save_relationship(self, rel: Relationship) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO relationships
                (persona_id, user_id, type, intimacy_score, trust_level,
                 interaction_count, last_active, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel.persona_id,
                    rel.user_id,
                    rel.type,
                    rel.intimacy_score,
                    rel.trust_level,
                    rel.interaction_count,
                    _dt(rel.last_active),
                    rel.notes,
                ),
            )
            await db.commit()

    # ============ 工具方法 ============

    def _row_to_entry(self, row: tuple) -> MemoryEntry:
        return MemoryEntry(
            id=row[0],
            content=row[1],
            role=row[2],
            source_user=row[3],
            memory_type=MemoryType(row[4]),
            importance=row[5],
            decay_rate=row[6],
            priority=row[7],
            access_count=row[8],
            is_forgotten=bool(row[9]),
            visibility=Visibility(row[10]),
            custom_policies=row[11].split("|") if row[11] else [],
            emotional_tags=row[12].split("|") if row[12] else [],
            related_memories=row[13].split("|") if row[13] else [],
            created_at=_parse_dt(row[14]) or datetime.now(timezone.utc),
            last_accessed=_parse_dt(row[15]),
            expires_at=_parse_dt(row[16]),
        )

    def _row_to_relationship(self, row: tuple) -> Relationship:
        return Relationship(
            persona_id=row[0],
            user_id=row[1],
            type=row[2],
            intimacy_score=row[3],
            trust_level=row[4],
            interaction_count=row[5],
            last_active=_parse_dt(row[6]),
            notes=row[7] or "",
        )
