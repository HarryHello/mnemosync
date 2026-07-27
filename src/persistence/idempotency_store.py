"""幂等键存储 (v0.3.0 Sub-Phase B).

群聊机器人平台 (AstrBot / MaiBot 等) 在网络抖动时可能重发同一条消息;
OpenAI 兼容端点上, 一次重发 = 一次完整的记忆图执行 + 一次上游 LLM 调用 —
既烧钱又会产生重复记忆。幂等表以 (integration_id, external_event_id) 为主键,
把首次成功响应缓存下来, 重发时原样返回。

  * integration_id: 集成标识, 取 api_key.id (一个 Key = 一个前台接入)。
  * external_event_id: 平台侧事件唯一标识 (从消息内容按策略提取, 如 QQ 消息 ID)。
  * response_text: 首次生成的 assistant 回复 (重放用; 失败不写入, 允许重试再生成)。

记录随 conversation_turns 同样的时间窗清理 (prune_before), 避免无限增长。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Iterator

import aiosqlite


@dataclass
class IdempotencyRecord:
    """一条幂等缓存记录."""

    integration_id: str
    external_event_id: str
    event_id: str  # 服务器侧生成的事件 ID (chatcmpl-*)
    response_text: str | None
    response_message: str | None = None  # JSON: 完整 assistant message (含 tool_calls)
    finish_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SqliteIdempotencyStore:
    """幂等键的 SQLite 存储 (独立库 data/idempotency.db).

    独立库的原因与 conversation_store 相同: 幂等检查在每次请求的关键路径上,
    与记忆/对话库隔离避免 WAL 相互干扰。
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
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                integration_id TEXT NOT NULL,
                external_event_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                response_text TEXT,
                response_message TEXT,
                finish_reason TEXT,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (integration_id, external_event_id)
            )
        """)
        migrations = (
            "ALTER TABLE idempotency_keys ADD COLUMN response_message TEXT",
            "ALTER TABLE idempotency_keys ADD COLUMN finish_reason TEXT",
        )
        for ddl in migrations:
            try:
                await db.execute(ddl)
            except aiosqlite.OperationalError:
                pass
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_idempotency_created "
            "ON idempotency_keys(created_at)"
        )

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def get(
        self, integration_id: str, external_event_id: str,
    ) -> IdempotencyRecord | None:
        """查询幂等缓存. 命中返回记录, 未命中返回 None."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT integration_id, external_event_id, event_id, response_text, "
                "response_message, finish_reason, created_at "
                "FROM idempotency_keys WHERE integration_id = ? AND external_event_id = ?",
                (integration_id, external_event_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            return IdempotencyRecord(
                integration_id=row[0],
                external_event_id=row[1],
                event_id=row[2],
                response_text=row[3],
                response_message=row[4],
                finish_reason=row[5],
                created_at=_parse_dt(row[6]),
            )

    async def record(
        self,
        integration_id: str,
        external_event_id: str,
        event_id: str,
        response_text: str | None,
        response_message: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        """写入幂等记录. 已存在时忽略 (INSERT OR IGNORE), 保留首次结果."""
        now = datetime.now(UTC)
        async with self._conn() as db:
            await db.execute(
                "INSERT OR IGNORE INTO idempotency_keys "
                "(integration_id, external_event_id, event_id, response_text, "
                "response_message, finish_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    integration_id, external_event_id, event_id, response_text,
                    response_message, finish_reason, now.isoformat(),
                ),
            )
            await db.commit()

    async def prune_before(self, cutoff: datetime) -> int:
        """删除 cutoff 之前的记录, 返回删除数."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM idempotency_keys WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return cur.rowcount or 0

    async def count(self) -> int:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM idempotency_keys") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0


def _parse_dt(v: str | None) -> datetime:
    if not v:
        return datetime.now(UTC)
    return datetime.fromisoformat(v)
