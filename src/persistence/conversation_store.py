"""短期对话流水存储 (v0.2.6, v0.3.0 空间化).

Mnemosync 是"跨前端统一记忆"中间件: 所有前端 (AstrBot / AIRI / Web / SDK ...)
本质上对应同一个用户与同一个人格. 客户端可能是不可控的黑盒 — 有的会传完整历史,
有的每次只传当前一句. 服务器必须自己维护一条连续对话流, 让上游看到的始终是
"这个人跨渠道说过什么", 而不是任一前端片面的会话。

结构上就一张 append-only 表, source_frontend 仅作元数据 (取 api_key.note),
不参与查询条件, 仅用于 debug / 回顾。

v0.3.0 空间事件流: 多用户场景下按 space_id 分区 — 群聊是一个 space, 装填
上下文只读本空间的流水 (list_for_space), 避免其他空间对话泄入上下文。
每条 space 内的轮次在提交时分配单调递增的 committed_sequence (MAX+1,
同事务内分配), 事件时间早于空间内最新已提交时间时标记 late_arrival。
external_event_id 记录平台侧事件 ID, 与幂等表呼应。无 space_id 的私聊/
非归属轮次不分配序号, 仍按 ts 定序。

生命周期:
  * 装填上下文时按时间窗 (默认 7d) 拉近段, 再按模型 context_length 从头部裁剪。
  * 每天一次后台任务删掉时间窗以外的记录。
  * 面板可主动清空 (提供 admin 端点)。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

import aiosqlite


@dataclass
class ConversationTurn:
    """一条对话轮次记录."""

    id: int | None
    role: str  # "user" | "assistant"
    content: str
    ts: datetime
    token_count: int
    source_frontend: str | None
    actor_id: str | None = None
    space_id: str | None = None
    external_event_id: str | None = None
    committed_sequence: int | None = None  # 空间内提交序号 (v0.3.0), 仅 space_id 非空时分配
    late_arrival: bool = False  # 事件时间早于空间内最新已提交时间 (乱序到达)


class SqliteConversationStore:
    """短期对话流水的 SQLite 存储 (append-only + 按时间清理).

    与 memory_store 分库存放 (data/conversation.db 独立), 是因为对话流水读写
    频率与量级都远高于长期记忆, 独立库避免 WAL 与 vacuum 相互干扰。
    """

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
                late_arrival INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_ts "
            "ON conversation_turns(ts DESC)"
        )
        # v0.3.0: 向后兼容迁移 (必须在索引创建之前 — 老库还没有新列)
        for ddl in (
            "ALTER TABLE conversation_turns ADD COLUMN actor_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN space_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN external_event_id TEXT",
            "ALTER TABLE conversation_turns ADD COLUMN committed_sequence INTEGER",
            "ALTER TABLE conversation_turns ADD COLUMN late_arrival INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                await db.execute(ddl)
            except aiosqlite.OperationalError:
                pass
        # v0.3.0: 空间事件流 — 按 (space_id, committed_sequence) 定序
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_space_seq "
            "ON conversation_turns(space_id, committed_sequence)"
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
    ) -> int:
        """追加一条对话轮次, 返回 rowid.

        空间事件流 (v0.3.0): space_id 非空时, 在同一事务内按 space 分区
        分配 committed_sequence (MAX+1), 并根据事件时间与空间内最新已提交
        时间的比较标记 late_arrival。space_id 为空 (私聊/非归属) 时序号留空,
        仍按 ts 定序。
        """
        if role not in ("user", "assistant"):
            raise ValueError(f"invalid role: {role!r}")
        event_ts = ts or datetime.now(timezone.utc)
        stamp = event_ts.isoformat()

        committed_sequence: int | None = None
        late_arrival = 0
        async with self._conn() as db:
            if space_id:
                async with db.execute(
                    "SELECT COALESCE(MAX(committed_sequence), -1) + 1, "
                    "MAX(ts) FROM conversation_turns WHERE space_id = ?",
                    (space_id,),
                ) as cur:
                    row = await cur.fetchone()
                committed_sequence = int(row[0]) if row and row[0] is not None else 0
                latest_ts = row[1] if row else None
                if latest_ts and stamp < latest_ts:
                    late_arrival = 1

            cur = await db.execute(
                "INSERT INTO conversation_turns "
                "(role, content, ts, token_count, source_frontend, actor_id, space_id, "
                " external_event_id, committed_sequence, late_arrival) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    role, content, stamp, int(token_count), source_frontend,
                    actor_id, space_id, external_event_id,
                    committed_sequence, late_arrival,
                ),
            )
            await db.commit()
            return cur.lastrowid or 0

    async def list_for_space(
        self,
        space_id: str,
        since: datetime | None = None,
        limit: int = 5000,
    ) -> list[ConversationTurn]:
        """按 committed_sequence 升序列出某个空间的对话流 (v0.3.0).

        群聊/多用户场景下, 装填上下文只读本空间的流水 — 不能把其他空间
        (其他群、私聊) 的对话泄进当前上下文。since 非空时叠加时间窗过滤
        (与 list_since 的口径一致)。
        """
        if since is not None:
            sql = (
                "SELECT id, role, content, ts, token_count, source_frontend, "
                "actor_id, space_id, external_event_id, committed_sequence, late_arrival "
                "FROM conversation_turns WHERE space_id = ? AND ts >= ? "
                "ORDER BY committed_sequence ASC, id ASC LIMIT ?"
            )
            params: tuple = (space_id, since.isoformat(), limit)
        else:
            sql = (
                "SELECT id, role, content, ts, token_count, source_frontend, "
                "actor_id, space_id, external_event_id, committed_sequence, late_arrival "
                "FROM conversation_turns WHERE space_id = ? "
                "ORDER BY committed_sequence ASC, id ASC LIMIT ?"
            )
            params = (space_id, limit)
        async with self._conn() as db:
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
                return [self._row_to_turn(r) for r in rows]

    async def list_since(
        self, since: datetime, limit: int = 1000
    ) -> list[ConversationTurn]:
        """按时间升序列出 since (UTC) 之后的对话. 装填上下文用."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, role, content, ts, token_count, source_frontend, "
                "actor_id, space_id, external_event_id, committed_sequence, late_arrival "
                "FROM conversation_turns WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since.isoformat(), limit),
            ) as cur:
                rows = await cur.fetchall()
                return [self._row_to_turn(r) for r in rows]

    async def list_recent(self, limit: int = 100) -> list[ConversationTurn]:
        """按时间降序列出最近 N 条 (调试面板用)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, role, content, ts, token_count, source_frontend, "
                "actor_id, space_id, external_event_id, committed_sequence, late_arrival "
                "FROM conversation_turns ORDER BY ts DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                return [self._row_to_turn(r) for r in rows]

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        role: str | None = None,
        source_frontend: str | None = None,
        sort_by: str = "ts",
        sort_order: str = "desc",
    ) -> tuple[list[ConversationTurn], int]:
        """面板分页: 返回 (当前页, 匹配总数).

        sort_by 白名单: ts / role / token_count / source_frontend / id.
        sort_order: 'asc' / 'desc'. 非法值退回默认 (ts, desc).
        source_frontend: 精确匹配 api_key.note 写入的来源标签 (None = 全部).
        """
        allowed_sort = {"ts", "role", "token_count", "source_frontend", "id"}
        sort_col = sort_by if sort_by in allowed_sort else "ts"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        where: list[str] = []
        params: list = []
        if role in ("user", "assistant"):
            where.append("role = ?")
            params.append(role)
        if source_frontend is not None:
            where.append("source_frontend = ?")
            params.append(source_frontend)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""

        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM conversation_turns{where_sql}",
                tuple(params),
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"SELECT id, role, content, ts, token_count, source_frontend, "
                f"actor_id, space_id, external_event_id, committed_sequence, late_arrival "
                f"FROM conversation_turns{where_sql} "
                f"ORDER BY {sort_col} {direction}, id ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                rows = await cur.fetchall()
                items = [self._row_to_turn(r) for r in rows]

        return items, total

    async def list_source_frontends(self) -> list[str]:
        """列出所有出现过的 source_frontend 值 (去重, 按字典序).

        面板 "来源" 列 header filter 用. NULL 排除 — 前端把它视为 "未标注",
        用户想过滤未标注可以走 role 或直接看列表。
        """
        async with self._conn() as db:
            async with db.execute(
                "SELECT DISTINCT source_frontend FROM conversation_turns "
                "WHERE source_frontend IS NOT NULL AND source_frontend != '' "
                "ORDER BY source_frontend ASC"
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    async def delete_by_id(self, turn_id: int) -> bool:
        """删除单条对话轮次, 返回是否命中."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM conversation_turns WHERE id = ?",
                (turn_id,),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def delete_by_ids(self, turn_ids: list[int]) -> int:
        """批量删除指定 id 的对话轮次, 返回删除条数."""
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
        """删除 cutoff 之前的所有记录, 返回删除数."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM conversation_turns WHERE ts < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return cur.rowcount or 0

    async def delete_all(self) -> int:
        """清空所有对话流水 (面板"重置连续记忆")."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM conversation_turns")
            await db.commit()
            return cur.rowcount or 0

    async def count(self) -> int:
        async with self._conn() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM conversation_turns"
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    @staticmethod
    def _row_to_turn(row: tuple) -> ConversationTurn:
        ts_raw = row[3]
        ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(timezone.utc)
        return ConversationTurn(
            id=row[0],
            role=row[1],
            content=row[2],
            ts=ts,
            token_count=row[4],
            source_frontend=row[5],
            actor_id=row[6] if len(row) > 6 else None,
            space_id=row[7] if len(row) > 7 else None,
            external_event_id=row[8] if len(row) > 8 else None,
            committed_sequence=row[9] if len(row) > 9 else None,
            late_arrival=bool(row[10]) if len(row) > 10 else False,
        )
