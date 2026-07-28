"""结构化人格存储.

persona_versions 表存储 PersonaDefinition 的版本化记录。
当前只有一个激活版本 (active=1)，更新时写入新版本，旧版本保留用于回滚。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.core.persona.definition import PersonaDefinition
from src.persistence.base import SqliteStore

logger = logging.getLogger(__name__)


class SqlitePersonaStore(SqliteStore):
    """SQLite 结构化人格存储."""

    @staticmethod
    async def _init_schema(db) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS persona_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                definition TEXT NOT NULL,
                changelog TEXT,
                author TEXT,
                created_at TIMESTAMP NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_active "
            "ON persona_versions(active)"
        )

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def get_active(self) -> PersonaDefinition | None:
        """获取当前激活的人格版本."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT definition, name, version, id FROM persona_versions WHERE active = 1 LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            try:
                d = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                return None
            defn = PersonaDefinition.from_dict(d)
            defn.name = row[1]
            defn.version = row[2]
            return defn

    async def save(self, definition: PersonaDefinition, *, changelog: str = "",
                    author: str | None = None) -> int:
        """写入新版本, 自动递增版本号, 标记为激活."""
        now = datetime.now(UTC)
        definition.updated_at = now
        definition.author = author or definition.author
        json_str = json.dumps(definition.to_dict(), ensure_ascii=False)

        async with self._conn() as db:
            # 旧版本失活
            await db.execute(
                "UPDATE persona_versions SET active = 0 WHERE active = 1"
            )
            # 写入新版本
            cur = await db.execute(
                "INSERT INTO persona_versions "
                "(version, name, definition, changelog, author, created_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (definition.version, definition.name, json_str, changelog,
                 author, now.isoformat()),
            )
            await db.commit()
            return cur.lastrowid or 0

    async def list_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        """列出所有版本 (不含 definition 全量)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, version, name, changelog, author, created_at, active "
                "FROM persona_versions ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "version": r[1],
                "name": r[2],
                "changelog": r[3],
                "author": r[4],
                "created_at": r[5],
                "active": bool(r[6]),
            }
            for r in rows
        ]

    async def get_version(self, version_id: int) -> PersonaDefinition | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT definition FROM persona_versions WHERE id = ?",
                (version_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            try:
                d = json.loads(row[0])
                return PersonaDefinition.from_dict(d)
            except (json.JSONDecodeError, TypeError):
                return None

    async def rollback(self, version_id: int) -> bool:
        """回滚到指定版本 (标记为激活, 旧版本失活)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id FROM persona_versions WHERE id = ?", (version_id,)
            ) as cur:
                if await cur.fetchone() is None:
                    return False
            await db.execute("UPDATE persona_versions SET active = 0 WHERE active = 1")
            await db.execute(
                "UPDATE persona_versions SET active = 1 WHERE id = ?",
                (version_id,),
            )
            await db.commit()
            return True

    async def count(self) -> int:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM persona_versions") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0
