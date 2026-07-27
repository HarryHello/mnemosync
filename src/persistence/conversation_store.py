"""短期对话事件流存储.

平台插件先把客户端快照拆成逐说话者事件；本层负责单事务批量写入、指纹去重、
空间内提交序号和迟到标记。旧表中的记录保留为 ``origin=legacy``。
"""

from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Iterator

import aiosqlite


@dataclass
class ConversationTurn:
    """一条规范化对话事件."""

    id: int | None
    role: str
    content: str
    ts: datetime
    token_count: int
    source_frontend: str | None
    actor_id: str | None = None
    effective_user_id: str | None = None
    display_name_snapshot: str | None = None
    external_key_snapshot: str | None = None
    space_id: str | None = None
    external_event_id: str | None = None
    origin: str = "legacy"
    event_fingerprint: str | None = None
    observed_at: datetime | None = None
    request_id: str | None = None
    committed_sequence: int | None = None
    late_arrival: bool = False
    interaction_id: str | None = None       # 逻辑交互 ID (同一根消息的多次 HTTP 请求)
    event_type: str = "message"             # message | tool_call | tool_result
    tool_call_id: str | None = None         # tool_call 对应的 call id (仅 tool_call 事件)


@dataclass
class ConversationEvent:
    """待写入的对话事件."""

    role: str
    content: str
    token_count: int
    source_frontend: str | None = None
    ts: datetime | None = None
    actor_id: str | None = None
    effective_user_id: str | None = None
    display_name_snapshot: str | None = None
    external_key_snapshot: str | None = None
    space_id: str | None = None
    external_event_id: str | None = None
    origin: str = "current"
    event_fingerprint: str | None = None
    observed_at: datetime | None = None
    request_id: str | None = None
    interaction_id: str | None = None
    event_type: str = "message"             # message | tool_call | tool_result
    tool_call_id: str | None = None


@dataclass(frozen=True)
class EventInsertResult:
    inserted: int
    duplicates: int
    row_ids: list[int]


def _utc_iso(value: datetime) -> str:
    """统一为 UTC ISO 文本，确保 SQLite TEXT 排序等同于时间排序."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def build_event_fingerprint(event: ConversationEvent) -> str:
    """构建跨请求稳定指纹；时间按分钟归一以兼容 AstrBot 的时间精度差异."""
    ts = (event.ts or datetime.now(UTC)).astimezone(UTC)
    minute = ts.replace(second=0, microsecond=0).isoformat()
    speaker = (
        event.external_key_snapshot
        or event.display_name_snapshot
        or event.actor_id
        or "unknown"
    ).strip().casefold()
    content = re.sub(r"\s+", " ", event.content).strip()
    material = "\x1f".join((
        event.source_frontend or "",
        event.space_id or "",
        event.role,
        speaker,
        minute,
        content,
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


_SELECT_COLUMNS = (
    "id, role, content, ts, token_count, source_frontend, actor_id, space_id, "
    "external_event_id, committed_sequence, late_arrival, effective_user_id, "
    "display_name_snapshot, external_key_snapshot, origin, event_fingerprint, "
    "observed_at, request_id, interaction_id, event_type, tool_call_id"
)


class SqliteConversationStore:
    """高频 append-only 对话事件存储."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._db is not None:
            return
        from pathlib import Path

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._init_schema(self._db)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def _conn(self) -> Iterator[aiosqlite.Connection]:
        if self._db is not None:
            yield self._db
        else:
            async with aiosqlite.connect(self.db_path) as db:
                yield db

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TIMESTAMP NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                source_frontend TEXT,
                actor_id TEXT,
                space_id TEXT,
                external_event_id TEXT,
                committed_sequence INTEGER,
                late_arrival INTEGER NOT NULL DEFAULT 0,
                effective_user_id TEXT,
                display_name_snapshot TEXT,
                external_key_snapshot TEXT,
                origin TEXT NOT NULL DEFAULT 'legacy',
                event_fingerprint TEXT,
                observed_at TIMESTAMP,
                request_id TEXT
            )
        """)
        migrations = (
            "ALTER TABLE conversation_turns ADD COLUMN actor_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN space_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN external_event_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN committed_sequence INTEGER",
            "ALTER TABLE conversation_turns ADD COLUMN late_arrival INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE conversation_turns ADD COLUMN effective_user_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN display_name_snapshot TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN external_key_snapshot TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE conversation_turns ADD COLUMN event_fingerprint TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN observed_at TIMESTAMP",
            "ALTER TABLE conversation_turns ADD COLUMN request_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN interaction_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN event_type TEXT NOT NULL DEFAULT 'message'",
            "ALTER TABLE conversation_turns ADD COLUMN tool_call_id TEXT",
        )
        for ddl in migrations:
            try:
                await db.execute(ddl)
            except aiosqlite.OperationalError:
                pass
        await db.execute(
            "UPDATE conversation_turns SET observed_at = ts WHERE observed_at IS NULL"
        )
        # 早期结构化事件混用了 +08:00 与 +00:00 ISO 文本。SQLite 对 TEXT
        # 按字典序排序，不会换算时区；启动迁移统一为 UTC，之后普通索引排序即可。
        async with db.execute(
            "SELECT id, ts, observed_at FROM conversation_turns "
            "WHERE ts NOT LIKE '%+00:00' OR "
            "(observed_at IS NOT NULL AND observed_at NOT LIKE '%+00:00')"
        ) as cur:
            mixed_timezone_rows = await cur.fetchall()
        for row_id, ts_raw, observed_raw in mixed_timezone_rows:
            updates: list[str] = []
            params: list[str | int] = []
            try:
                ts_utc = _utc_iso(datetime.fromisoformat(ts_raw))
                if ts_utc != ts_raw:
                    updates.append("ts = ?")
                    params.append(ts_utc)
            except (TypeError, ValueError):
                pass
            try:
                observed_utc = _utc_iso(datetime.fromisoformat(observed_raw))
                if observed_utc != observed_raw:
                    updates.append("observed_at = ?")
                    params.append(observed_utc)
            except (TypeError, ValueError):
                pass
            if updates:
                params.append(row_id)
                await db.execute(
                    f"UPDATE conversation_turns SET {', '.join(updates)} WHERE id = ?",
                    tuple(params),
                )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_ts "
            "ON conversation_turns(ts DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_space_seq "
            "ON conversation_turns(space_id, committed_sequence)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_space_ts "
            "ON conversation_turns(space_id, ts, id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_actor ON conversation_turns(actor_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_effective_user "
            "ON conversation_turns(effective_user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_request ON conversation_turns(request_id)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_event_fingerprint "
            "ON conversation_turns(event_fingerprint) WHERE event_fingerprint IS NOT NULL"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_interaction "
            "ON conversation_turns(interaction_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_event_type "
            "ON conversation_turns(event_type)"
        )

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def append(
        self,
        role: str,
        content: str,
        token_count: int,
        source_frontend: str | None = None,
        ts: datetime | None = None,
        actor_id: str | None = None,
        space_id: str | None = None,
        external_event_id: str | None = None,
        effective_user_id: str | None = None,
        display_name_snapshot: str | None = None,
        external_key_snapshot: str | None = None,
        origin: str | None = None,
        event_fingerprint: str | None = None,
        observed_at: datetime | None = None,
        request_id: str | None = None,
        interaction_id: str | None = None,
        event_type: str = "message",
        tool_call_id: str | None = None,
    ) -> int:
        """追加单条事件；高吞吐插件路径应使用 ``append_events``."""
        event = ConversationEvent(
            role=role,
            content=content,
            token_count=token_count,
            source_frontend=source_frontend,
            ts=ts,
            actor_id=actor_id,
            effective_user_id=effective_user_id,
            display_name_snapshot=display_name_snapshot,
            external_key_snapshot=external_key_snapshot,
            space_id=space_id,
            external_event_id=external_event_id,
            origin=origin or ("assistant" if role == "assistant" else "current"),
            event_fingerprint=event_fingerprint,
            observed_at=observed_at,
            request_id=request_id,
            interaction_id=interaction_id,
            event_type=event_type,
            tool_call_id=tool_call_id,
        )
        result = await self.append_events([event])
        return result.row_ids[0] if result.row_ids else 0

    async def append_events(self, events: list[ConversationEvent]) -> EventInsertResult:
        """单事务批量写入事件，并按指纹跳过重复快照."""
        if not events:
            return EventInsertResult(inserted=0, duplicates=0, row_ids=[])
        for event in events:
            if event.role not in ("user", "assistant"):
                raise ValueError(f"invalid role: {event.role!r}")

        inserted = 0
        duplicates = 0
        row_ids: list[int] = []
        space_state: dict[str, tuple[int, str | None]] = {}
        now = datetime.now(UTC)

        async with self._conn() as db:
            for event in events:
                event_ts = event.ts or now
                stamp = _utc_iso(event_ts)
                observed = _utc_iso(event.observed_at or event_ts)
                sequence: int | None = None
                late_arrival = 0

                if event.space_id:
                    state = space_state.get(event.space_id)
                    if state is None:
                        async with db.execute(
                            "SELECT COALESCE(MAX(committed_sequence), -1) + 1, MAX(ts) "
                            "FROM conversation_turns WHERE space_id = ?",
                            (event.space_id,),
                        ) as cur:
                            row = await cur.fetchone()
                        state = (
                            int(row[0]) if row and row[0] is not None else 0,
                            row[1] if row else None,
                        )
                    sequence, latest_ts = state
                    late_arrival = int(bool(latest_ts and stamp < latest_ts))

                cur = await db.execute(
                    "INSERT OR IGNORE INTO conversation_turns "
                    "(role, content, ts, token_count, source_frontend, actor_id, space_id, "
                    "external_event_id, committed_sequence, late_arrival, effective_user_id, "
                    "display_name_snapshot, external_key_snapshot, origin, event_fingerprint, "
                    "observed_at, request_id, interaction_id, event_type, tool_call_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.role, event.content, stamp, int(event.token_count),
                        event.source_frontend, event.actor_id, event.space_id,
                        event.external_event_id, sequence, late_arrival,
                        event.effective_user_id, event.display_name_snapshot,
                        event.external_key_snapshot, event.origin,
                        event.event_fingerprint, observed, event.request_id,
                        event.interaction_id, event.event_type, event.tool_call_id,
                    ),
                )
                if (cur.rowcount or 0) == 0:
                    duplicates += 1
                    continue

                inserted += 1
                row_ids.append(cur.lastrowid or 0)
                if event.space_id and sequence is not None:
                    _, latest_ts = space_state.get(event.space_id, (sequence, None))
                    newest = max(latest_ts, stamp) if latest_ts else stamp
                    space_state[event.space_id] = (sequence + 1, newest)

            await db.commit()

        return EventInsertResult(inserted=inserted, duplicates=duplicates, row_ids=row_ids)

    async def list_by_interaction(
        self, interaction_id: str, event_type: str | None = None
    ) -> list[ConversationTurn]:
        """列出同一逻辑交互中的所有事件."""
        where = "interaction_id = ?"
        params: list = [interaction_id]
        if event_type:
            where += " AND event_type = ?"
            params.append(event_type)
        async with self._conn() as db:
            async with db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM conversation_turns WHERE {where} "
                "ORDER BY ts ASC, id ASC",
                tuple(params),
            ) as cur:
                return [self._row_to_turn(row) for row in await cur.fetchall()]

    async def get_interaction_for_tool_call(self, tool_call_id: str) -> str | None:
        """根据 tool_call_id 找到首次生成该调用的逻辑交互 ID."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT interaction_id FROM conversation_turns "
                "WHERE tool_call_id = ? AND event_type = 'tool_call' LIMIT 1",
                (tool_call_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def get_latest_tool_interaction(self, space_id: str, limit: int = 5) -> str | None:
        """返回最近有工具事件的逻辑交互 ID（供参考）."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT DISTINCT interaction_id FROM conversation_turns "
                "WHERE space_id = ? AND event_type != 'message' "
                "ORDER BY ts DESC LIMIT ?",
                (space_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                return rows[0][0] if rows else None

    async def list_for_space(
        self,
        space_id: str,
        since: datetime | None = None,
        limit: int = 5000,
    ) -> list[ConversationTurn]:
        where = "space_id = ?"
        params: list = [space_id]
        if since is not None:
            where += " AND ts >= ?"
            params.append(since.isoformat())
        params.append(limit)
        async with self._conn() as db:
            async with db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM conversation_turns WHERE {where} "
                "ORDER BY ts ASC, id ASC LIMIT ?",
                tuple(params),
            ) as cur:
                return [self._row_to_turn(row) for row in await cur.fetchall()]

    async def list_since(self, since: datetime, limit: int = 1000) -> list[ConversationTurn]:
        async with self._conn() as db:
            async with db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM conversation_turns "
                "WHERE ts >= ? ORDER BY ts ASC, id ASC LIMIT ?",
                (since.isoformat(), limit),
            ) as cur:
                return [self._row_to_turn(row) for row in await cur.fetchall()]

    async def list_recent(self, limit: int = 100) -> list[ConversationTurn]:
        async with self._conn() as db:
            async with db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM conversation_turns "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ) as cur:
                return [self._row_to_turn(row) for row in await cur.fetchall()]

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        role: str | None = None,
        source_frontend: str | None = None,
        actor_id: str | None = None,
        effective_user_id: str | None = None,
        space_id: str | None = None,
        origin: str | None = None,
        interaction_id: str | None = None,
        event_type: str | None = None,
        sort_by: str = "ts",
        sort_order: str = "desc",
    ) -> tuple[list[ConversationTurn], int]:
        allowed_sort = {
            "ts", "role", "token_count", "source_frontend", "id",
            "origin", "display_name_snapshot", "committed_sequence",
        }
        sort_col = sort_by if sort_by in allowed_sort else "ts"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"
        where: list[str] = []
        params: list = []
        filters = (
            ("role", role if role in ("user", "assistant") else None),
            ("source_frontend", source_frontend),
            ("actor_id", actor_id),
            ("effective_user_id", effective_user_id),
            ("space_id", space_id),
            ("origin", origin),
            ("interaction_id", interaction_id),
            ("event_type", event_type),
        )
        for column, value in filters:
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        # 默认查询不包含工具中间事件
        if not event_type:
            where.append("event_type = 'message'")
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""

        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM conversation_turns{where_sql}", tuple(params)
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM conversation_turns{where_sql} "
                f"ORDER BY {sort_col} {direction}, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                items = [self._row_to_turn(row) for row in await cur.fetchall()]
        return items, total

    async def list_source_frontends(self) -> list[str]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT DISTINCT source_frontend FROM conversation_turns "
                "WHERE source_frontend IS NOT NULL AND source_frontend != '' "
                "ORDER BY source_frontend ASC"
            ) as cur:
                return [row[0] for row in await cur.fetchall()]

    async def delete_by_id(self, turn_id: int) -> bool:
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM conversation_turns WHERE id = ?", (turn_id,))
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def delete_by_ids(self, turn_ids: list[int]) -> int:
        if not turn_ids:
            return 0
        placeholders = ", ".join("?" for _ in turn_ids)
        async with self._conn() as db:
            cur = await db.execute(
                f"DELETE FROM conversation_turns WHERE id IN ({placeholders})",
                tuple(turn_ids),
            )
            await db.commit()
            return cur.rowcount or 0

    async def delete_before(self, cutoff: datetime) -> int:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM conversation_turns WHERE ts < ?", (cutoff.isoformat(),)
            )
            await db.commit()
            return cur.rowcount or 0

    async def delete_all(self) -> int:
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM conversation_turns")
            await db.commit()
            return cur.rowcount or 0

    async def count(self) -> int:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM conversation_turns") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    @staticmethod
    def _row_to_turn(row: tuple) -> ConversationTurn:
        ts = datetime.fromisoformat(row[3]) if row[3] else datetime.now(UTC)
        observed_at = datetime.fromisoformat(row[16]) if row[16] else ts
        return ConversationTurn(
            id=row[0],
            role=row[1],
            content=row[2],
            ts=ts,
            token_count=row[4],
            source_frontend=row[5],
            actor_id=row[6],
            space_id=row[7],
            external_event_id=row[8],
            committed_sequence=row[9],
            late_arrival=bool(row[10]),
            effective_user_id=row[11],
            display_name_snapshot=row[12],
            external_key_snapshot=row[13],
            origin=row[14] or "legacy",
            event_fingerprint=row[15],
            observed_at=observed_at,
            request_id=row[17],
            interaction_id=row[18] if len(row) > 18 else None,
            event_type=row[19] if len(row) > 19 else "message",
            tool_call_id=row[20] if len(row) > 20 else None,
        )
