"""关系持久化存储.

SQLite 存储关系状态 (relationships 表) + 关系审计日志 (relationship_audit_log 表).
与 SqliteMemoryStore 共享同一个数据库文件.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.core.memory.models import Relationship, RelationshipAuditEntry
from src.persistence.base import SqliteStore, _dt, _parse_dt, resolve_sort_params


class SqliteRelationshipStore(SqliteStore):
    """SQLite 关系存储实现 — Relationship CRUD + 审计日志.

    与 SqliteMemoryStore 共享同一个数据库文件 (relationships 表 + relationship_audit_log 表).
    """

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
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

        # 命名迁移: 幂等 ADD COLUMN (捕获 duplicate column 错误)
        from src.persistence.migrations import MigrationRunner, add_column_if_missing

        await MigrationRunner([
            ("002_add_persona_addressing", add_column_if_missing("relationships", "persona_addressing", "TEXT")),
            ("003_add_user_addressing", add_column_if_missing("relationships", "user_addressing", "TEXT")),
            ("004_add_context", add_column_if_missing("relationships", "context", "TEXT")),
        ]).apply(db)

    async def init_db(self) -> None:
        """兼容旧接口: 幂等地初始化 schema. 长连接模式下 connect() 已包含此步骤."""
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    # ============ Relationship CRUD ============

    async def get_relationship(self, persona_id: str, user_id: str) -> Relationship | None:
        """按 (persona_id, user_id) 查询一条关系; 不存在返回 None."""
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
        """以 upsert 方式保存一条关系 (INSERT OR REPLACE)."""
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

    async def migrate_relationships_to_group(
        self,
        persona_id: str,
        actor_id: str,
        group_id: str,
    ) -> int:
        """将指定 Actor 的关系数据迁移到 UserGroup (绑定后调用).

        当 Actor 被绑定到 UserGroup 后, effective_user_id 变为 group_id.
        但此前以 actor_id 为 user_id 存储的关系行仍然存在, 导致同一人
        出现两条独立关系 (绑定前 + 绑定后).

        迁移策略:
        - 找到所有 persona_id + user_id = actor_id 的关系行
        - 若 group_id 已有关系行, 合并两行 (取较高亲密度/信任度,
          累加交互次数, 保留最新 last_active, 非空 addressing 优先)
        - 删除旧的 actor_id 行
        - 返回迁移/合并的关系数

        Args:
            persona_id: 人格 ID
            actor_id: 旧 user_id (绑定前的 actor_id)
            group_id: 新 user_id (绑定后的 group_id)

        Returns:
            受影响的关系行数
        """
        async with self._conn() as db:
            # 1. 查出 actor 现有的所有关系
            async with db.execute(
                """
                SELECT persona_id, user_id, type, intimacy_score, trust_level,
                       interaction_count, last_active, notes,
                       persona_addressing, user_addressing, context
                FROM relationships
                WHERE persona_id = ? AND user_id = ?
                """,
                (persona_id, actor_id),
            ) as cur:
                actor_rows = list(await cur.fetchall())

            if not actor_rows:
                return 0  # 没有需迁移的关系

            # 2. 尝试加载已有的 group_id 关系行 (每个 persona 最多一个)
            async with db.execute(
                """
                SELECT persona_id, user_id, type, intimacy_score, trust_level,
                       interaction_count, last_active, notes,
                       persona_addressing, user_addressing, context
                FROM relationships
                WHERE persona_id = ? AND user_id = ?
                """,
                (persona_id, group_id),
            ) as cur:
                existing = await cur.fetchone()

            for row in actor_rows:
                p_id, u_id, r_type, intimacy, trust, icount, last_active, notes, \
                    p_addr, u_addr, ctx = row

                if existing:
                    # 合并: 取较高的 intimacy/trust, 累加 interaction_count,
                    # 取更新的 last_active, 非空 addressing/context 优先
                    e_pid, e_uid, e_type, e_intimacy, e_trust, e_icount, \
                        e_last, e_notes, e_p_addr, e_u_addr, e_ctx = existing

                    merged_intimacy = max(intimacy, e_intimacy)
                    merged_trust = max(trust, e_trust)
                    merged_icount = icount + e_icount
                    merged_last = max(
                        _parse_dt(last_active) or datetime.min,
                        _parse_dt(e_last) or datetime.min,
                    )
                    merged_notes = (notes or "") + ("; " + e_notes if e_notes else "")
                    merged_p_addr = p_addr or e_p_addr
                    merged_u_addr = u_addr or e_u_addr
                    merged_ctx = ctx or e_ctx

                    # 更新 group_id 行
                    await db.execute(
                        """
                        UPDATE relationships
                        SET type = ?, intimacy_score = ?, trust_level = ?,
                            interaction_count = ?, last_active = ?, notes = ?,
                            persona_addressing = ?, user_addressing = ?, context = ?
                        WHERE persona_id = ? AND user_id = ?
                        """,
                        (
                            r_type, merged_intimacy, merged_trust,
                            merged_icount, merged_last.isoformat(), merged_notes,
                            merged_p_addr, merged_u_addr, merged_ctx,
                            persona_id, group_id,
                        ),
                    )
                else:
                    # 直接更新 user_id: actor_id → group_id
                    await db.execute(
                        """
                        UPDATE relationships
                        SET user_id = ?
                        WHERE persona_id = ? AND user_id = ?
                        """,
                        (group_id, persona_id, actor_id),
                    )

                # 删除旧 actor_id 行 (已合并或已改 user_id)
                await db.execute(
                    "DELETE FROM relationships WHERE persona_id = ? AND user_id = ?",
                    (persona_id, actor_id),
                )

            await db.commit()
            return len(actor_rows)

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

        now = datetime.now(UTC)
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
                current = dict.fromkeys(self._ADDRESSING_FIELDS)
            else:
                current = dict(zip(self._ADDRESSING_FIELDS, row, strict=False))

            for field_name, new_val in proposals.items():
                if field_name not in self._ADDRESSING_FIELDS:
                    raise ValueError(f"非法字段: {field_name}")
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
                changed_at=_parse_dt(r[3]) or datetime.now(UTC),
                source=r[4],
                field_name=r[5],
                old_value=r[6],
                new_value=r[7],
                reason=r[8],
            )
            for r in rows
        ]

    # ============ 批量清理 (人格状态重置) ============

    async def delete_all_relationships(self) -> int:
        """清空所有关系状态. 用于人格状态重置."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM relationships")
            await db.commit()
            return cur.rowcount or 0

    async def count_relationships(self) -> int:
        """返回所有关系行的总数 (用于重置仪表盘)."""
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
        sort_col, direction = resolve_sort_params(
            sort_by, sort_order,
            {
                "intimacy_score": "intimacy_score",
                "trust_level": "trust_level",
                "interaction_count": "interaction_count",
                "last_active": "last_active",
                "user_id": "user_id",
                "type": "type",
            },
            default_col="intimacy_score",
        )

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

    def _row_to_relationship(self, row: Sequence[Any]) -> Relationship:
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
