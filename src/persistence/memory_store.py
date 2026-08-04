"""记忆持久化存储.

SQLite 存储记忆元数据 + 关系状态.
向量数据由 infra/vector_store.py 存入 ChromaDB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from src.core.constants import MEMORY_ACTIVE_PRIORITY_THRESHOLD
from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.persistence.base import SqliteStore, _dt, _parse_dt, resolve_sort_params


class SqliteMemoryStore(SqliteStore):
    """SQLite 记忆存储实现 — 仅 MemoryEntry CRUD.

    使用方式:
      * 长连接单例 (推荐, API 层): 应用启动时 ``await store.connect()``, 关闭时 ``await store.close()``.
        所有方法共用同一条 aiosqlite 连接, 无每请求 open/close 开销.
      * 短连接 (CLI / 一次性脚本 / 测试): 不调 ``connect()``, 每次方法内部临时开连接 (旧行为).
    """

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
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_space ON memory_entries(space_id)"
        )

        # 命名迁移: 幂等 ADD COLUMN (捕获 duplicate column 错误)
        from src.persistence.migrations import MigrationRunner, add_column_if_missing

        await MigrationRunner([
            ("001_add_space_id", add_column_if_missing("memory_entries", "space_id", "TEXT")),
            ("005_add_superseded_by", add_column_if_missing("memory_entries", "superseded_by", "TEXT")),
        ]).apply(db)

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_superseded_by "
            "ON memory_entries(superseded_by) WHERE superseded_by IS NOT NULL"
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
                 space_id, superseded_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    entry.superseded_by,
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
            ts = before if before.tzinfo else before.replace(tzinfo=UTC)
            conditions.append("created_at < ?")
            params.append(ts.astimezone(UTC).isoformat())
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
                  AND superseded_by IS NULL
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
                  AND superseded_by IS NULL
                  AND priority > ?
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (source_user, MEMORY_ACTIVE_PRIORITY_THRESHOLD, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_entry(r) for r in rows]

    async def list_for_decay(self, skip_hours: int = 24, limit: int = 50) -> list[MemoryEntry]:
        """列出待衰减评估的普通记忆（跳过 skip_hours 小时内新建的）."""
        threshold = (datetime.now(UTC) - timedelta(hours=skip_hours)).isoformat()
        async with self._conn() as db:
            async with db.execute(
                """
                SELECT * FROM memory_entries
                WHERE memory_type = 'normal' AND is_forgotten = 0
                  AND superseded_by IS NULL
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

    async def mark_superseded(self, old_id: str, new_id: str) -> bool:
        """标记一条记忆被新记忆替代 (软替代, 不物理删除).

        Args:
            old_id: 被替代的记忆 ID
            new_id: 替代它的新记忆 ID

        Returns:
            是否成功更新
        """
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE memory_entries SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
                (new_id, old_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_supersede_chain(self, memory_id: str) -> list[MemoryEntry]:
        """获取一条记忆的替代链 (从该记忆开始, 追溯所有替代它的版本).

        返回顺序: 原始记忆 -> 替代它的新记忆 -> 更新的替代 -> ...
        """
        chain: list[MemoryEntry] = []
        current = await self.get_by_id(memory_id)
        if current is None:
            return chain
        chain.append(current)
        visited = {memory_id}
        # 向前追溯: 沿 superseded_by 指针找到替代版本
        while current.superseded_by:
            next_id = current.superseded_by
            if next_id in visited:
                break
            next_entry = await self.get_by_id(next_id)
            if next_entry is None:
                break
            chain.append(next_entry)
            visited.add(next_id)
            current = next_entry
        return chain

    async def mark_accessed(self, entry_id: str) -> None:
        """标记一条记忆被访问 (access_count + 1, 更新 last_accessed)."""
        now = datetime.now(UTC).isoformat()
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
                  AND superseded_by IS NULL
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
        before: datetime | None = None,
        after: datetime | None = None,
    ) -> tuple[list[MemoryEntry], int]:
        """面板分页: 返回 (当前页, 匹配总数). is_forgotten=0 过滤.

        sort_by 白名单: created_at / last_accessed / importance / decay_rate /
        access_count / memory_type / source_user. 非法值退回 created_at.
        sort_order: 'asc' / 'desc', 其它值退回 desc.
        """
        sort_col, direction = resolve_sort_params(
            sort_by, sort_order,
            {
                "created_at": "created_at",
                "last_accessed": "last_accessed",
                "importance": "importance",
                "decay_rate": "decay_rate",
                "access_count": "access_count",
                "memory_type": "memory_type",
                "source_user": "source_user",
            },
            default_col="created_at",
        )

        where = ["is_forgotten = 0", "superseded_by IS NULL"]
        params: list = []
        if source_user:
            where.insert(0, "source_user = ?")
            params.append(source_user)
        if memory_type:
            where.append("memory_type = ?")
            params.append(memory_type)
        if before:
            ts = before if before.tzinfo else before.replace(tzinfo=UTC)
            where.append("created_at < ?")
            params.append(ts.astimezone(UTC).isoformat())
        if after:
            ts = after if after.tzinfo else after.replace(tzinfo=UTC)
            where.append("created_at > ?")
            params.append(ts.astimezone(UTC).isoformat())
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

    async def list_distinct_source_users(self) -> list[str]:
        """返回 memory_entries 表中所有活跃记忆的 source_user 去重列表."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT DISTINCT source_user FROM memory_entries "
                "WHERE source_user IS NOT NULL AND source_user != '' "
                "AND is_forgotten = 0 AND superseded_by IS NULL "
                "ORDER BY source_user ASC"
            ) as cur:
                return [row[0] for row in await cur.fetchall()]

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

    # ============ 批量清理 (人格状态重置) ============

    async def delete_all_memories(self) -> int:
        """清空所有记忆 (含 PERMANENT). 用于人格状态重置."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM memory_entries")
            await db.commit()
            return cur.rowcount or 0

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
            created_at=_parse_dt(row[14]) or datetime.now(UTC),
            last_accessed=_parse_dt(row[15]),
            expires_at=_parse_dt(row[16]),
            space_id=row[17] if len(row) > 17 else None,
            superseded_by=row[18] if len(row) > 18 else None,
        )
