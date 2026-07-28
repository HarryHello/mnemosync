"""记忆持久化存储.

SQLite 存储记忆元数据 + 关系状态.
向量数据由 infra/vector_store.py 存入 ChromaDB.
"""

from __future__ import annotations

import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.memory.models import (
    DecayState,
    MemoryEntry,
    MemoryType,
    Relationship,
    RelationshipAuditEntry,
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

    使用方式:
      * 长连接单例 (推荐, API 层): 应用启动时 ``await store.connect()``, 关闭时 ``await store.close()``.
        所有方法共用同一条 aiosqlite 连接, 无每请求 open/close 开销.
      * 短连接 (CLI / 一次性脚本 / 测试): 不调 ``connect()``, 每次方法内部临时开连接 (旧行为).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """建立长连接并初始化 schema (幂等)."""
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
        """内部连接上下文: 长连接模式复用 self._db, 否则临时开连接 (旧行为)."""
        if self._db is not None:
            yield self._db
        else:
            async with aiosqlite.connect(self.db_path) as db:
                yield db

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
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
                expires_at TIMESTAMP,
                space_id TEXT
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
        # v0.3.0: 向后兼容迁移
        try:
            await db.execute("ALTER TABLE memory_entries ADD COLUMN space_id TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_space ON memory_entries(space_id)"
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
        # v0.2.10: 动态称呼演化. NULL = 沿用 TOML 基线.
        # SQLite 不支持 ADD COLUMN IF NOT EXISTS, 手动查 PRAGMA table_info.
        async with db.execute("PRAGMA table_info(relationships)") as cur:
            existing_cols = {row[1] async for row in cur}
        for col in ("persona_addressing", "user_addressing", "context"):
            if col not in existing_cols:
                await db.execute(
                    f"ALTER TABLE relationships ADD COLUMN {col} TEXT"
                )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS relationship_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                changed_at TIMESTAMP NOT NULL,
                source TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                reason TEXT NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_persona_user "
            "ON relationship_audit_log(persona_id, user_id, changed_at DESC)"
        )

    async def init_db(self) -> None:
        """兼容旧接口: 幂等地初始化 schema. 长连接模式下 connect() 已包含此步骤."""
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    # ============ MemoryEntry CRUD ============

    async def save(self, entry: MemoryEntry) -> None:
        async with self._conn() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO memory_entries
                (id, content, role, source_user, memory_type, importance, decay_rate,
                 priority, access_count, is_forgotten, visibility, custom_policies,
                 emotional_tags, related_memories, created_at, last_accessed, expires_at,
                 space_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    entry.space_id,
                ),
            )
            await db.commit()

    async def get_by_id(self, entry_id: str) -> MemoryEntry | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_entry(row) if row else None

    async def delete(self, entry_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def delete_by_user(
        self,
        source_user: str,
        *,
        memory_type: str | None = None,
        before: datetime | None = None,
    ) -> int:
        """批量删除指定用户的记忆.

        Args:
            source_user: effective_user_id
            memory_type: 可选, 仅删除指定类型 (permanent/normal)
            before: 可选, 仅删除此时间之前创建的记忆

        Returns:
            删除的行数
        """
        conditions = ["source_user = ?"]
        params: list[Any] = [source_user]
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        if before:
            from datetime import timezone as _tz
            ts = before if before.tzinfo else before.replace(tzinfo=_tz.utc)
            conditions.append("created_at < ?")
            params.append(ts.astimezone(_tz.utc).isoformat())
        where = " AND ".join(conditions)
        async with self._conn() as db:
            cur = await db.execute(
                f"DELETE FROM memory_entries WHERE {where}",
                tuple(params),
            )
            await db.commit()
            return cur.rowcount or 0

    async def list_permanent(
        self,
        source_user: str | None,
        limit: int = 7,
        space_id: str | None = None,
    ) -> list[MemoryEntry]:
        """加载永久记忆（按重要性降序）.

        v0.3.0 受众放宽 (粗筛): 返回 自己桶 + PUBLIC (+ 群聊时本空间共享,
        不含 SOURCE_RESTRICTED) 的并集。精确的可见性判定 (关系门槛 /
        custom_policies) 由调用方走 AudienceFilter.filter 完成。
        """
        conditions = ["visibility = 'public'"]
        params: list = []
        if source_user:
            conditions.append("source_user = ?")
            params.append(source_user)
        if space_id:
            conditions.append("(space_id = ? AND visibility != 'source_restricted')")
            params.append(space_id)
        where = " OR ".join(conditions)
        async with self._conn() as db:
            async with db.execute(
                f"""
                SELECT * FROM memory_entries
                WHERE memory_type = 'permanent' AND is_forgotten = 0
                  AND ({where})
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    async def list_active_normal(
        self, source_user: str, limit: int = 5
    ) -> list[MemoryEntry]:
        """加载用户 ACTIVE 状态的普通记忆（按优先级降序）.

        用于主对话 Agent 上下文拼装（当无语义检索时）.
        """
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM memory_entries
                WHERE source_user = ? AND memory_type = 'permanent'
                """,
                (source_user,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def count_all(self) -> int:
        """总记忆数 (含遗忘, 用于仪表盘)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM memory_entries"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def list_all_for_user(self, source_user: str, limit: int = 20) -> list[MemoryEntry]:
        """列出用户的所有未遗忘记忆（调试用）."""
        async with self._conn() as db:
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

    async def list_page_for_user(
        self,
        source_user: str,
        *,
        limit: int,
        offset: int,
        memory_type: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[MemoryEntry], int]:
        """面板分页: 返回 (当前页, 匹配总数). is_forgotten=0 过滤.

        sort_by 白名单: created_at / last_accessed / importance / decay_rate /
        access_count / memory_type / source_user. 非法值退回 created_at.
        sort_order: 'asc' / 'desc', 其它值退回 desc.
        """
        allowed_sort = {
            "created_at",
            "last_accessed",
            "importance",
            "decay_rate",
            "access_count",
            "memory_type",
            "source_user",
        }
        sort_col = sort_by if sort_by in allowed_sort else "created_at"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        where = ["source_user = ?", "is_forgotten = 0"]
        params: list = [source_user]
        if memory_type:
            where.append("memory_type = ?")
            params.append(memory_type)
        where_sql = " AND ".join(where)

        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM memory_entries WHERE {where_sql}",
                tuple(params),
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"""
                SELECT * FROM memory_entries
                WHERE {where_sql}
                ORDER BY {sort_col} {direction}, id ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                items = [self._row_to_entry(r) for r in rows]

        return items, total

    async def iter_all(self, batch_size: int = 200):
        """按 created_at 升序分批产出所有记忆 (含遗忘). 用于 reindex/prune 遍历."""
        offset = 0
        while True:
            async with self._conn() as db:
                async with db.execute(
                    "SELECT * FROM memory_entries ORDER BY created_at ASC LIMIT ? OFFSET ?",
                    (batch_size, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                break
            for r in rows:
                yield self._row_to_entry(r)
            if len(rows) < batch_size:
                break
            offset += batch_size

    # ============ Relationship CRUD ============

    async def get_relationship(self, persona_id: str, user_id: str) -> Relationship | None:
        async with self._conn() as db:
            async with db.execute(
                """
                SELECT persona_id, user_id, type, intimacy_score, trust_level,
                       interaction_count, last_active, notes,
                       persona_addressing, user_addressing, context
                FROM relationships WHERE persona_id = ? AND user_id = ?
                """,
                (persona_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_relationship(row) if row else None

    async def save_relationship(self, rel: Relationship) -> None:
        async with self._conn() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO relationships
                (persona_id, user_id, type, intimacy_score, trust_level,
                 interaction_count, last_active, notes,
                 persona_addressing, user_addressing, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    rel.persona_addressing,
                    rel.user_addressing,
                    rel.context,
                ),
            )
            await db.commit()

    # ============ Relationship 称呼动态演化 (v0.2.10) ============

    _ADDRESSING_FIELDS = ("persona_addressing", "user_addressing", "context")

    async def update_relationship_addressing(
        self,
        persona_id: str,
        user_id: str,
        *,
        persona_addressing: str | None = None,
        user_addressing: str | None = None,
        context: str | None = None,
        source: str,
        reason: str,
    ) -> list[RelationshipAuditEntry]:
        """更新关系的称呼/背景字段, 同步写审计日志.

        None = 该字段本次不改 (保持旧值).
        source ∈ {'agent', 'manual'}. reason 由调用方保证非空.

        Returns:
            本次实际写入的 audit 条目 (改了几个字段就有几条).
        """
        if source not in ("agent", "manual"):
            raise ValueError(f"source must be 'agent' or 'manual', got {source!r}")
        proposals = {
            "persona_addressing": persona_addressing,
            "user_addressing": user_addressing,
            "context": context,
        }
        if all(v is None for v in proposals.values()):
            raise ValueError("至少需要传入一个待更新字段")

        now = datetime.now(timezone.utc)
        audit_entries: list[RelationshipAuditEntry] = []
        async with self._conn() as db:
            # 读现状: 若无 relationship 行则创建 stranger 基线
            async with db.execute(
                "SELECT persona_addressing, user_addressing, context "
                "FROM relationships WHERE persona_id = ? AND user_id = ?",
                (persona_id, user_id),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                # 建行, 用 Relationship.create 的默认值
                new_rel = Relationship.create(persona_id, user_id)
                await db.execute(
                    """
                    INSERT INTO relationships
                    (persona_id, user_id, type, intimacy_score, trust_level,
                     interaction_count, last_active, notes,
                     persona_addressing, user_addressing, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (
                        new_rel.persona_id,
                        new_rel.user_id,
                        new_rel.type,
                        new_rel.intimacy_score,
                        new_rel.trust_level,
                        new_rel.interaction_count,
                        _dt(new_rel.last_active),
                        new_rel.notes,
                    ),
                )
                current = {k: None for k in self._ADDRESSING_FIELDS}
            else:
                current = dict(zip(self._ADDRESSING_FIELDS, row))

            for field_name, new_val in proposals.items():
                if new_val is None:
                    continue
                old_val = current[field_name]
                if old_val == new_val:
                    continue  # 无变化则跳过写入 (audit 也不记)
                await db.execute(
                    f"UPDATE relationships SET {field_name} = ? "
                    "WHERE persona_id = ? AND user_id = ?",
                    (new_val, persona_id, user_id),
                )
                cur = await db.execute(
                    """
                    INSERT INTO relationship_audit_log
                    (persona_id, user_id, changed_at, source,
                     field_name, old_value, new_value, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        persona_id,
                        user_id,
                        _dt(now),
                        source,
                        field_name,
                        old_val,
                        new_val,
                        reason,
                    ),
                )
                audit_entries.append(
                    RelationshipAuditEntry(
                        id=cur.lastrowid or 0,
                        persona_id=persona_id,
                        user_id=user_id,
                        changed_at=now,
                        source=source,
                        field_name=field_name,
                        old_value=old_val,
                        new_value=new_val,
                        reason=reason,
                    )
                )
            await db.commit()
        return audit_entries

    async def list_relationship_audit(
        self, persona_id: str, user_id: str, limit: int = 20
    ) -> list[RelationshipAuditEntry]:
        """按 changed_at 倒序返回审计条目."""
        async with self._conn() as db:
            async with db.execute(
                """
                SELECT id, persona_id, user_id, changed_at, source,
                       field_name, old_value, new_value, reason
                FROM relationship_audit_log
                WHERE persona_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (persona_id, user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [
            RelationshipAuditEntry(
                id=r[0],
                persona_id=r[1],
                user_id=r[2],
                changed_at=_parse_dt(r[3]) or datetime.now(timezone.utc),
                source=r[4],
                field_name=r[5],
                old_value=r[6],
                new_value=r[7],
                reason=r[8],
            )
            for r in rows
        ]

    # ============ 批量清理 (人格状态重置) ============

    async def delete_all_memories(self) -> int:
        """清空所有记忆 (含 PERMANENT). 用于人格状态重置."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM memory_entries")
            await db.commit()
            return cur.rowcount or 0

    async def delete_all_relationships(self) -> int:
        """清空所有关系状态. 用于人格状态重置."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM relationships")
            await db.commit()
            return cur.rowcount or 0

    async def count_relationships(self) -> int:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM relationships") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def list_relationships(
        self,
        persona_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "intimacy_score",
        sort_order: str = "desc",
    ) -> tuple[list[Relationship], int]:
        """面板分页: 返回 (当前页, 匹配总数). 仅返回指定 persona 的关系.

        sort_by 白名单: intimacy_score / trust_level / interaction_count /
        last_active / user_id / type. 非法值退回 intimacy_score.
        sort_order: 'asc' / 'desc', 其它值退回 desc.
        """
        allowed_sort = {
            "intimacy_score", "trust_level", "interaction_count",
            "last_active", "user_id", "type",
        }
        sort_col = sort_by if sort_by in allowed_sort else "intimacy_score"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        async with self._conn() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM relationships WHERE persona_id = ?",
                (persona_id,),
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"""
                SELECT persona_id, user_id, type, intimacy_score, trust_level,
                       interaction_count, last_active, notes,
                       persona_addressing, user_addressing, context
                FROM relationships
                WHERE persona_id = ?
                ORDER BY {sort_col} {direction}, user_id ASC
                LIMIT ? OFFSET ?
                """,
                (persona_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                items = [self._row_to_relationship(r) for r in rows]

        return items, total

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
            space_id=row[17] if len(row) > 17 else None,
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
            persona_addressing=row[8] if len(row) > 8 else None,
            user_addressing=row[9] if len(row) > 9 else None,
            context=row[10] if len(row) > 10 else None,
        )
