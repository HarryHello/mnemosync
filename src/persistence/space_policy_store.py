"""空间社交策略存储.

SocialPolicy 按空间定制表达行为:
- Expressor 启用/禁用
- Expressor temperature
- 首选的回复最大长度
- 是否使用表情符号

每个空间一条记录, 独立于人格定义和工具策略。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite

from src.persistence.base import SqliteStore

logger = logging.getLogger(__name__)


@dataclass
class SpacePolicy:
    """单个空间的社交策略."""

    space_id: str
    expressor_enabled: bool = True
    expressor_temperature: float = 0.4
    preferred_max_length: int | None = 200
    use_emojis: bool | None = True
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SqliteSpacePolicyStore(SqliteStore):
    """SQLite 空间社交策略存储."""

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        from src.persistence.migrations import MigrationRunner

        async def _migrate(db: aiosqlite.Connection) -> None:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS space_policies (
                    space_id TEXT PRIMARY KEY,
                    config TEXT NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMP NOT NULL
                )
            """)

        await MigrationRunner([
            ("001_create_space_policies", _migrate),
        ]).apply(db)

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def get(self, space_id: str) -> SpacePolicy | None:
        if not space_id:
            return None
        async with self._conn() as db:
            async with db.execute(
                "SELECT space_id, config, updated_at FROM space_policies WHERE space_id = ?",
                (space_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        config = json.loads(row[1]) if row[1] else {}
        return SpacePolicy(
            space_id=row[0],
            expressor_enabled=config.get("expressor_enabled", True),
            expressor_temperature=config.get("expressor_temperature", 0.4),
            preferred_max_length=config.get("preferred_max_length", 200),
            use_emojis=config.get("use_emojis", True),
            updated_at=datetime.fromisoformat(row[2]) if row[2] else datetime.now(UTC),
        )

    async def upsert(self, policy: SpacePolicy) -> None:
        now = datetime.now(UTC)
        policy.updated_at = now
        config = {
            "expressor_enabled": policy.expressor_enabled,
            "expressor_temperature": policy.expressor_temperature,
            "preferred_max_length": policy.preferred_max_length,
            "use_emojis": policy.use_emojis,
        }
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO space_policies (space_id, config, updated_at) VALUES (?, ?, ?)",
                (policy.space_id, json.dumps(config, ensure_ascii=False), now.isoformat()),
            )
            await db.commit()

    async def delete(self, space_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM space_policies WHERE space_id = ?", (space_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_all(self, limit: int = 100) -> list[SpacePolicy]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT space_id, config, updated_at FROM space_policies ORDER BY space_id LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        result = []
        for row in rows:
            config = json.loads(row[1]) if row[1] else {}
            result.append(SpacePolicy(
                space_id=row[0],
                expressor_enabled=config.get("expressor_enabled", True),
                expressor_temperature=config.get("expressor_temperature", 0.4),
                preferred_max_length=config.get("preferred_max_length", 200),
                use_emojis=config.get("use_emojis", True),
                updated_at=datetime.fromisoformat(row[2]) if row[2] else datetime.now(UTC),
            ))
        return result
