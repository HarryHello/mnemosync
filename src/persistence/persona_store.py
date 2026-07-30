"""结构化人格存储.

persona_versions 表存储 PersonaDefinition 的版本化记录, 属于特定人格 profile.

v0.4.0 新增 ``personas`` 表作为人格注册表, 支持多个人格 profile 共存与切换:
  - ``personas`` 表: 人格 profile 元数据 (名称/描述/活跃标记)
  - ``persona_versions`` 表: 每个版本通过 ``persona_id`` 关联到对应人格 profile

迁移路径:
  - 首次启动时创建 ``personas`` 表, 并为 ``persona_versions`` 添加 ``persona_id`` 列
  - 已有数据自动关联到新创建的默认人格
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import aiosqlite

from src.core.persona.definition import PersonaDefinition
from src.persistence.base import SqliteStore
from src.persistence.migrations import MigrationRunner, add_column_if_missing

logger = logging.getLogger(__name__)


async def _migrate_create_personas_table(db: aiosqlite.Connection) -> None:
    """创建 personas 表并迁移已有数据."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS personas (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    # 检查是否已有数据需要迁移
    async with db.execute("SELECT COUNT(*) FROM personas") as cur:
        row = await cur.fetchone()
        if row and row[0] > 0:
            return  # 已有数据，跳过

    # 从现有 persona_versions 获取名称创建默认人格
    async with db.execute(
        "SELECT name FROM persona_versions WHERE active = 1 LIMIT 1"
    ) as cur:
        ver_row = await cur.fetchone()

    name = ver_row[0] if ver_row else "默认人格"
    now = datetime.now(UTC).isoformat()
    default_id = uuid.uuid4().hex[:16]
    await db.execute(
        "INSERT INTO personas (id, name, description, is_active, created_at, updated_at) "
        "VALUES (?, ?, '', 1, ?, ?)",
        (default_id, name, now, now),
    )
    # 已有版本关联到默认人格
    await db.execute(
        "UPDATE persona_versions SET persona_id = ? WHERE persona_id IS NULL",
        (default_id,),
    )


class SqlitePersonaStore(SqliteStore):
    """SQLite 结构化人格存储 (支持多人格 profile)."""

    _MIGRATIONS: list[tuple[str, Any]] = [
        (
            "001_add_persona_id_column",
            add_column_if_missing("persona_versions", "persona_id", "TEXT DEFAULT NULL"),
        ),
        ("002_create_personas_table", _migrate_create_personas_table),
    ]

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
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
        # 应用多人格迁移
        await MigrationRunner(SqlitePersonaStore._MIGRATIONS).apply(db)

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    # ===========================================================================
    # Persona Profile CRUD (v0.4.0)
    # ===========================================================================

    async def list_personas(self) -> list[dict[str, Any]]:
        """列出所有人格 profile."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, name, description, is_active, created_at, updated_at "
                "FROM personas ORDER BY is_active DESC, created_at ASC"
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "description": r[2],
                "is_active": bool(r[3]),
                "created_at": r[4],
                "updated_at": r[5],
            }
            for r in rows
        ]

    async def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        """获取单个人格 profile 元数据."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, name, description, is_active, created_at, updated_at "
                "FROM personas WHERE id = ?",
                (persona_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "is_active": bool(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    async def create_persona(
        self, name: str, description: str = "",
    ) -> str:
        """创建新人格 profile, 返回 id."""
        persona_id = uuid.uuid4().hex[:16]
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO personas (id, name, description, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (persona_id, name, description, now, now),
            )
            await db.commit()
        return persona_id

    async def update_persona(
        self, persona_id: str, name: str | None = None, description: str | None = None,
    ) -> bool:
        """更新人格 profile 元数据. 只更新非 None 字段. 返回是否找到."""
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            if name is not None and description is not None:
                cur = await db.execute(
                    "UPDATE personas SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                    (name, description, now, persona_id),
                )
            elif name is not None:
                cur = await db.execute(
                    "UPDATE personas SET name = ?, updated_at = ? WHERE id = ?",
                    (name, now, persona_id),
                )
            elif description is not None:
                cur = await db.execute(
                    "UPDATE personas SET description = ?, updated_at = ? WHERE id = ?",
                    (description, now, persona_id),
                )
            else:
                return False  # 无更新字段
            await db.commit()
            return cur.rowcount > 0

    async def activate_persona(self, persona_id: str) -> bool:
        """切换到目标人格 (设 is_active=1, 其他设为 0). 返回是否找到."""
        async with self._conn() as db:
            # 检查目标存在
            async with db.execute(
                "SELECT id FROM personas WHERE id = ?", (persona_id,)
            ) as cur:
                if await cur.fetchone() is None:
                    return False
            # 全部失活
            await db.execute("UPDATE personas SET is_active = 0")
            # 激活目标
            await db.execute(
                "UPDATE personas SET is_active = 1, updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), persona_id),
            )
            await db.commit()
            return True

    async def delete_persona(self, persona_id: str) -> bool:
        """删除人格 profile 及其所有版本. 返回是否找到."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id FROM personas WHERE id = ?", (persona_id,)
            ) as cur:
                if await cur.fetchone() is None:
                    return False
            await db.execute("DELETE FROM persona_versions WHERE persona_id = ?", (persona_id,))
            await db.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
            await db.commit()
            return True

    async def get_active_persona(self) -> dict[str, Any] | None:
        """获取当前活跃的人格 profile (is_active=1)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, name, description, is_active, created_at, updated_at "
                "FROM personas WHERE is_active = 1 LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "is_active": bool(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    async def _ensure_default_persona(self) -> str:
        """确保至少有一个人格 profile. 没有时创建默认人格, 返回其 id."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id FROM personas LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
            if row:
                return cast(str, row[0])

        # 从当前 active persona_definition 的名称继承
        async with self._conn() as db:
            async with db.execute(
                "SELECT name FROM persona_versions WHERE active = 1 LIMIT 1"
            ) as cur:
                ver_row = await cur.fetchone()

        default_name = ver_row[0] if ver_row else "默认人格"
        return await self.create_persona(name=default_name)

    # ===========================================================================
    # Persona Definition CRUD
    # ===========================================================================

    async def get_active(self) -> PersonaDefinition | None:
        """获取当前活跃人格的活跃版本定义.

        v0.4.0 逻辑:
          1. 查询 ``personas`` 表中 ``is_active=1`` 的人格 profile
          2. 若没有活跃 profile, 尝试查询旧的 active=1 版本 (向后兼容)
          3. 从 ``persona_versions`` 加载该人格下 ``active=1`` 的版本
        """
        async with self._conn() as db:
            # 优先: 按活跃人格 profile 查找
            async with db.execute(
                "SELECT pv.definition, pv.name, pv.version, pv.id "
                "FROM persona_versions pv "
                "INNER JOIN personas p ON p.id = pv.persona_id "
                "WHERE p.is_active = 1 AND pv.active = 1 "
                "LIMIT 1"
            ) as cur:
                row = await cur.fetchone()

            # 回退: 旧的单人格模式 (无 personas 表记录时)
            if row is None:
                async with db.execute(
                    "SELECT definition, name, version, id "
                    "FROM persona_versions WHERE active = 1 LIMIT 1"
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
                    author: str | None = None,
                    persona_id: str | None = None) -> int:
        """写入新版本.

        Args:
            definition: 人格定义
            changelog: 变更说明
            author: 作者
            persona_id: 所属人格 profile id. 若为 None, 自动获取或创建默认人格.
        """
        now = datetime.now(UTC)
        definition.updated_at = now
        definition.author = author or definition.author
        json_str = json.dumps(definition.to_dict(), ensure_ascii=False)

        # 解析 persona_id
        resolved_pid = persona_id
        if resolved_pid is None:
            active = await self.get_active_persona()
            if active:
                resolved_pid = active["id"]
            else:
                resolved_pid = await self._ensure_default_persona()

        async with self._conn() as db:
            # 该人格下旧版本失活
            await db.execute(
                "UPDATE persona_versions SET active = 0 "
                "WHERE active = 1 AND persona_id = ?",
                (resolved_pid,),
            )
            # 如果有版本无 persona_id (旧数据), 一并失活
            await db.execute(
                "UPDATE persona_versions SET active = 0 "
                "WHERE active = 1 AND persona_id IS NULL",
            )
            # 写入新版本
            cur = await db.execute(
                "INSERT INTO persona_versions "
                "(version, name, definition, changelog, author, created_at, active, persona_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (definition.version, definition.name, json_str, changelog,
                 author, now.isoformat(), resolved_pid),
            )
            await db.commit()
            return cur.lastrowid or 0

    async def list_versions(self, limit: int = 50,
                            persona_id: str | None = None) -> list[dict[str, Any]]:
        """列出版本 (不含 definition 全量).

        Args:
            limit: 最大返回数
            persona_id: 筛选到指定人格. None 时返回全局版本.
        """
        async with self._conn() as db:
            if persona_id:
                async with db.execute(
                    "SELECT id, version, name, changelog, author, created_at, active "
                    "FROM persona_versions WHERE persona_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (persona_id, limit),
                ) as cur:
                    rows = await cur.fetchall()
            else:
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
            # 查询目标版本
            async with db.execute(
                "SELECT id, persona_id FROM persona_versions WHERE id = ?",
                (version_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return False
            target_persona_id = row[1]
            # 该人格下旧版本失活
            await db.execute(
                "UPDATE persona_versions SET active = 0 "
                "WHERE active = 1 AND persona_id = ?",
                (target_persona_id,),
            )
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
