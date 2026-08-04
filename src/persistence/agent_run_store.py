"""辅助 Agent 运行记录存储.

记录每次辅助 Agent 的运行: 超时、状态、工具调用轨迹和用量.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite

from src.persistence.base import SqliteStore

# 清理时保留的最大记录数 (超出后删除最旧记录)
MAX_RECORDS: int = 10000


@dataclass
class AgentRunRecord:
    """一条 Agent 运行记录."""

    run_id: str
    parent_request_id: str | None
    agent_name: str
    input_event_ids: list[str]
    base_version: str | None
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "ok", "failed", "timeout", "cancelled"]
    tool_trace: list[dict[str, Any]]
    usage: dict[str, Any]
    structured_result: Any | None
    error: str | None


class AgentRunStore(SqliteStore):
    """Agent 运行记录的 SQLite 存储."""

    _enable_foreign_keys = False

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        from src.persistence.migrations import MigrationRunner

        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                parent_request_id TEXT,
                agent_name TEXT NOT NULL,
                input_event_ids TEXT,
                base_version TEXT,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'running',
                tool_trace TEXT,
                usage TEXT,
                structured_result TEXT,
                error TEXT
            )
        """)

        async def _idx_parent(db: aiosqlite.Connection) -> None:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_parent "
                "ON agent_runs(parent_request_id)"
            )

        async def _idx_agent(db: aiosqlite.Connection) -> None:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_agent "
                "ON agent_runs(agent_name)"
            )

        async def _idx_started(db: aiosqlite.Connection) -> None:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_started "
                "ON agent_runs(started_at DESC)"
            )

        await MigrationRunner([
            ("001_create_idx_parent", _idx_parent),
            ("002_create_idx_agent", _idx_agent),
            ("003_create_idx_started", _idx_started),
        ]).apply(db)

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    # ── 写入 ────────────────────────────────────────────────────────────

    async def create_run(
        self,
        run_id: str,
        parent_request_id: str | None,
        agent_name: str,
        *,
        input_event_ids: list[str] | None = None,
        base_version: str | None = None,
    ) -> None:
        """创建一条 running 状态的运行记录."""
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO agent_runs "
                "(run_id, parent_request_id, agent_name, input_event_ids, "
                " base_version, started_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (
                    run_id,
                    parent_request_id,
                    agent_name,
                    json.dumps(input_event_ids or [], ensure_ascii=False),
                    base_version,
                    now,
                ),
            )
            await db.commit()

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        tool_trace: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
        structured_result: Any | None = None,
        error: str | None = None,
    ) -> None:
        """完成一条运行记录."""
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            await db.execute(
                "UPDATE agent_runs SET "
                "finished_at = ?, status = ?, tool_trace = ?, usage = ?, "
                "structured_result = ?, error = ? "
                "WHERE run_id = ?",
                (
                    now,
                    status,
                    json.dumps(tool_trace or [], ensure_ascii=False),
                    json.dumps(usage or {}, ensure_ascii=False),
                    json.dumps(structured_result, ensure_ascii=False)
                    if structured_result is not None
                    else None,
                    error,
                    run_id,
                ),
            )
            await db.commit()

    # ── 查询 ────────────────────────────────────────────────────────────

    async def get_by_id(self, run_id: str) -> AgentRunRecord | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
        return self._row_to_record(row) if row else None

    async def list_recent(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent_name: str | None = None,
        status: str | None = None,
    ) -> list[AgentRunRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        if agent_name:
            conditions.append("agent_name = ?")
            params.append(agent_name)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        async with self._conn() as db:
            async with db.execute(
                f"SELECT * FROM agent_runs WHERE {where} "
                f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_record(r) for r in rows]

    async def count(
        self,
        agent_name: str | None = None,
        status: str | None = None,
    ) -> int:
        conditions: list[str] = []
        params: list[Any] = []
        if agent_name:
            conditions.append("agent_name = ?")
            params.append(agent_name)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM agent_runs WHERE {where}", params
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def cleanup(
        self, retention_days: int = 7, max_records: int = MAX_RECORDS
    ) -> int:
        """清理旧记录, 返回被删条数."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM agent_runs "
                "WHERE started_at < datetime('now', ? || ' days')",
                (-retention_days,),
            )
            deleted = cur.rowcount or 0
            cur = await db.execute(
                "DELETE FROM agent_runs WHERE run_id NOT IN "
                "(SELECT run_id FROM agent_runs "
                "ORDER BY started_at DESC LIMIT ?)",
                (max_records,),
            )
            deleted += cur.rowcount or 0
            await db.commit()
        return deleted

    # ── 行转换 ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: Sequence[Any]) -> AgentRunRecord:
        return AgentRunRecord(
            run_id=row[0],
            parent_request_id=row[1],
            agent_name=row[2],
            input_event_ids=json.loads(row[3]) if row[3] else [],
            base_version=row[4],
            started_at=datetime.fromisoformat(row[5])
            if row[5]
            else datetime.now(UTC),
            finished_at=datetime.fromisoformat(row[6]) if row[6] else None,
            status=row[7] or "running",
            tool_trace=json.loads(row[8]) if row[8] else [],
            usage=json.loads(row[9]) if row[9] else {},
            structured_result=json.loads(row[10]) if row[10] else None,
            error=row[11],
        )
