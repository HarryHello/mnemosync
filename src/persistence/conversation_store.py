"""短期对话流水存储 (v0.2.6).

Mnemosync 是"跨前端统一记忆"中间件: 所有前端 (AstrBot / AIRI / Web / SDK ...)
本质上对应同一个用户与同一个人格. 客户端可能是不可控的黑盒 — 有的会传完整历史,
有的每次只传当前一句. 服务器必须自己维护一条连续对话流, 让上游看到的始终是
"这个人跨渠道说过什么", 而不是任一前端片面的会话。

结构上就一张 append-only 表 (id, role, content, ts, token_count, source_frontend),
所有前端写入同一 bucket, 无 thread / user 分区 (符合 v0.2.x 单人格单用户定位, 见
`mnemosync-single-persona-scope`). source_frontend 仅作元数据 (取 api_key.note),
不参与查询条件, 仅用于 debug / 回顾。

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
                source_frontend TEXT
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_ts "
            "ON conversation_turns(ts DESC)"
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
    ) -> int:
        """追加一条对话轮次, 返回 rowid."""
        if role not in ("user", "assistant"):
            raise ValueError(f"invalid role: {role!r}")
        stamp = (ts or datetime.now(timezone.utc)).isoformat()
        async with self._conn() as db:
            cur = await db.execute(
                "INSERT INTO conversation_turns (role, content, ts, token_count, source_frontend) "
                "VALUES (?, ?, ?, ?, ?)",
                (role, content, stamp, int(token_count), source_frontend),
            )
            await db.commit()
            return cur.lastrowid or 0

    async def list_since(
        self, since: datetime, limit: int = 1000
    ) -> list[ConversationTurn]:
        """按时间升序列出 since (UTC) 之后的对话. 装填上下文用."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, role, content, ts, token_count, source_frontend "
                "FROM conversation_turns WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since.isoformat(), limit),
            ) as cur:
                rows = await cur.fetchall()
                return [self._row_to_turn(r) for r in rows]

    async def list_recent(self, limit: int = 100) -> list[ConversationTurn]:
        """按时间降序列出最近 N 条 (调试面板用)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, role, content, ts, token_count, source_frontend "
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
    ) -> tuple[list[ConversationTurn], int]:
        """面板分页: 返回 (当前页, 匹配总数). ts DESC."""
        where_sql = ""
        params: list = []
        if role in ("user", "assistant"):
            where_sql = " WHERE role = ?"
            params.append(role)

        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM conversation_turns{where_sql}",
                tuple(params),
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"SELECT id, role, content, ts, token_count, source_frontend "
                f"FROM conversation_turns{where_sql} ORDER BY ts DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                rows = await cur.fetchall()
                items = [self._row_to_turn(r) for r in rows]

        return items, total

    async def delete_by_id(self, turn_id: int) -> bool:
        """删除单条对话轮次, 返回是否命中."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM conversation_turns WHERE id = ?",
                (turn_id,),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

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
        )
