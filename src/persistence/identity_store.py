"""身份持久化存储 (v0.3.0).

actors / user_groups / actor_group_memberships / identity_strategies 四张表的 CRUD.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import aiosqlite

from src.core.identity.models import (
    Actor,
    IdentityStrategy,
    UserGroup,
)
from src.persistence.base import SqliteStore


class SqliteIdentityStore(SqliteStore):
    """身份数据的 SQLite 存储."""

    _enable_foreign_keys = False

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS actors (
                id TEXT PRIMARY KEY,
                external_key TEXT NOT NULL,
                frontend TEXT NOT NULL,
                display_name TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE(frontend, external_key)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_actors_frontend_key
            ON actors(frontend, external_key)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                id TEXT PRIMARY KEY,
                name TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS actor_group_memberships (
                actor_id TEXT NOT NULL REFERENCES actors(id),
                group_id TEXT NOT NULL REFERENCES user_groups(id),
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (actor_id, group_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS identity_strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    # ============ Actor CRUD ============

    async def find_or_create_actor(
        self, external_key: str, frontend: str,
        display_name: str | None = None,
        metadata: str = "{}",
    ) -> Actor:
        """查找或创建 Actor。"""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, external_key, frontend, display_name, metadata, created_at, updated_at "
                "FROM actors WHERE frontend = ? AND external_key = ?",
                (frontend, external_key),
            ) as cur:
                row = await cur.fetchone()
            if row:
                # 存在则返回
                now = datetime.now(UTC)
                # 如果 display_name 更新了，写入
                if display_name and row[3] != display_name:
                    await db.execute(
                        "UPDATE actors SET display_name = ?, updated_at = ? WHERE id = ?",
                        (display_name, now.isoformat(), row[0]),
                    )
                    await db.commit()
                return Actor(
                    id=row[0], external_key=row[1], frontend=row[2],
                    display_name=row[3] if row[3] else display_name,
                    metadata=row[4] if row[4] != "{}" else metadata,
                    created_at=_parse_dt(row[5]),
                    updated_at=_parse_dt(row[6]),
                )
            # 不存在则创建
            now = datetime.now(UTC)
            actor_id = f"actor_{secrets.token_hex(12)}"
            await db.execute(
                "INSERT INTO actors (id, external_key, frontend, display_name, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (actor_id, external_key, frontend, display_name, metadata, now.isoformat(), now.isoformat()),
            )
            await db.commit()
            return Actor(
                id=actor_id, external_key=external_key, frontend=frontend,
                display_name=display_name, metadata=metadata,
                created_at=now, updated_at=now,
            )

    async def get_actor(self, actor_id: str) -> Actor | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, external_key, frontend, display_name, metadata, created_at, updated_at "
                "FROM actors WHERE id = ?",
                (actor_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            return Actor(
                id=row[0], external_key=row[1], frontend=row[2],
                display_name=row[3], metadata=row[4],
                created_at=_parse_dt(row[5]), updated_at=_parse_dt(row[6]),
            )

    async def find_unique_actor_by_display_name(
        self,
        frontend: str,
        display_name: str,
    ) -> Actor | None:
        """昵称在指定平台内唯一时返回 Actor；歧义或不存在时返回 None."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, external_key, frontend, display_name, metadata, created_at, updated_at "
                "FROM actors WHERE frontend = ? AND display_name = ? LIMIT 2",
                (frontend, display_name),
            ) as cur:
                rows = await cur.fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        return Actor(
            id=row[0], external_key=row[1], frontend=row[2],
            display_name=row[3], metadata=row[4],
            created_at=_parse_dt(row[5]), updated_at=_parse_dt(row[6]),
        )

    async def list_actors(self, limit: int = 50, offset: int = 0) -> tuple[list[Actor], int]:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM actors") as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                "SELECT id, external_key, frontend, display_name, metadata, created_at, updated_at "
                "FROM actors ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
            actors = [
                Actor(id=r[0], external_key=r[1], frontend=r[2],
                       display_name=r[3], metadata=r[4],
                       created_at=_parse_dt(r[5]), updated_at=_parse_dt(r[6]))
                for r in rows
            ]
            return actors, total

    # ============ UserGroup CRUD ============

    async def create_group(self, name: str | None = None) -> UserGroup:
        now = datetime.now(UTC)
        gid = f"group_{secrets.token_hex(12)}"
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO user_groups (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (gid, name, now.isoformat(), now.isoformat()),
            )
            await db.commit()
        return UserGroup(id=gid, name=name, created_at=now, updated_at=now)

    async def get_group(self, group_id: str) -> UserGroup | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, name, created_at, updated_at FROM user_groups WHERE id = ?",
                (group_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            return UserGroup(
                id=row[0], name=row[1],
                created_at=_parse_dt(row[2]), updated_at=_parse_dt(row[3]),
            )

    async def list_groups(self, limit: int = 50, offset: int = 0) -> tuple[list[UserGroup], int]:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM user_groups") as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                "SELECT id, name, created_at, updated_at FROM user_groups ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
            groups = [
                UserGroup(id=r[0], name=r[1],
                          created_at=_parse_dt(r[2]), updated_at=_parse_dt(r[3]))
                for r in rows
            ]
            return groups, total

    # ============ Membership CRUD ============

    async def bind_actor_to_group(self, actor_id: str, group_id: str) -> bool:
        """绑定 Actor 到 UserGroup。如果已存在返回 False。"""
        now = datetime.now(UTC)
        async with self._conn() as db:
            try:
                await db.execute(
                    "INSERT INTO actor_group_memberships (actor_id, group_id, created_at) VALUES (?, ?, ?)",
                    (actor_id, group_id, now.isoformat()),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def unbind_actor_from_group(self, actor_id: str, group_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM actor_group_memberships WHERE actor_id = ? AND group_id = ?",
                (actor_id, group_id),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def get_effective_user_id(self, actor_id: str) -> str:
        """计算有效用户 ID：如果 Actor 属于 UserGroup，返回 group_id，否则返回 actor_id。"""
        async with self._conn() as db:
            async with db.execute(
                "SELECT group_id FROM actor_group_memberships WHERE actor_id = ? LIMIT 1",
                (actor_id,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                return row[0]
            return actor_id

    async def list_actor_groups(self, actor_id: str) -> list[UserGroup]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT g.id, g.name, g.created_at, g.updated_at "
                "FROM user_groups g "
                "JOIN actor_group_memberships m ON m.group_id = g.id "
                "WHERE m.actor_id = ?",
                (actor_id,),
            ) as cur:
                rows = await cur.fetchall()
            return [
                UserGroup(id=r[0], name=r[1],
                          created_at=_parse_dt(r[2]), updated_at=_parse_dt(r[3]))
                for r in rows
            ]

    async def list_all_bound_actor_ids(self) -> list[tuple[str, str]]:
        """返回所有已绑定的 (actor_id, group_id) 对, 用于启动迁移."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT actor_id, group_id FROM actor_group_memberships"
            ) as cur:
                rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]

    async def list_group_members(self, group_id: str) -> list[Actor]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT a.id, a.external_key, a.frontend, a.display_name, a.metadata, a.created_at, a.updated_at "
                "FROM actors a "
                "JOIN actor_group_memberships m ON m.actor_id = a.id "
                "WHERE m.group_id = ?",
                (group_id,),
            ) as cur:
                rows = await cur.fetchall()
            return [
                Actor(id=r[0], external_key=r[1], frontend=r[2],
                       display_name=r[3], metadata=r[4],
                       created_at=_parse_dt(r[5]), updated_at=_parse_dt(r[6]))
                for r in rows
            ]

    async def resolve_user_identities(
        self,
        user_ids: list[str],
    ) -> dict[str, tuple[UserGroup | None, list[Actor]]]:
        """批量解析关系中的 effective_user_id 为可读身份.

        未绑定 UserGroup 的关系 ID 指向 Actor；绑定后的关系 ID 指向
        UserGroup，并返回该组下的全部平台账号。无法识别的旧 ID 不返回。
        """
        unique_ids = list(dict.fromkeys(user_ids))
        if not unique_ids:
            return {}

        actor_ids = [uid for uid in unique_ids if uid.startswith("actor_")]
        group_ids = [uid for uid in unique_ids if uid.startswith("group_")]
        resolved: dict[str, tuple[UserGroup | None, list[Actor]]] = {}

        async with self._conn() as db:
            if actor_ids:
                placeholders = ",".join("?" for _ in actor_ids)
                async with db.execute(
                    "SELECT id, external_key, frontend, display_name, metadata, created_at, updated_at "
                    f"FROM actors WHERE id IN ({placeholders})",
                    actor_ids,
                ) as cur:
                    rows = await cur.fetchall()
                for r in rows:
                    actor = Actor(
                        id=r[0], external_key=r[1], frontend=r[2],
                        display_name=r[3], metadata=r[4],
                        created_at=_parse_dt(r[5]), updated_at=_parse_dt(r[6]),
                    )
                    resolved[actor.id] = (None, [actor])

            if group_ids:
                placeholders = ",".join("?" for _ in group_ids)
                async with db.execute(
                    "SELECT id, name, created_at, updated_at "
                    f"FROM user_groups WHERE id IN ({placeholders})",
                    group_ids,
                ) as cur:
                    group_rows = await cur.fetchall()
                for r in group_rows:
                    group = UserGroup(
                        id=r[0], name=r[1],
                        created_at=_parse_dt(r[2]), updated_at=_parse_dt(r[3]),
                    )
                    resolved[group.id] = (group, [])

                async with db.execute(
                    "SELECT m.group_id, a.id, a.external_key, a.frontend, a.display_name, "
                    "a.metadata, a.created_at, a.updated_at "
                    "FROM actor_group_memberships m "
                    "JOIN actors a ON a.id = m.actor_id "
                    f"WHERE m.group_id IN ({placeholders}) "
                    "ORDER BY m.created_at ASC",
                    group_ids,
                ) as cur:
                    member_rows = await cur.fetchall()
                for r in member_rows:
                    entry = resolved.get(r[0])
                    if entry is None:
                        continue
                    entry[1].append(Actor(
                        id=r[1], external_key=r[2], frontend=r[3],
                        display_name=r[4], metadata=r[5],
                        created_at=_parse_dt(r[6]), updated_at=_parse_dt(r[7]),
                    ))

        return resolved

    # ============ IdentityStrategy CRUD ============

    async def create_strategy(
        self, name: str, strategy_type: str, config: str = "{}",
    ) -> IdentityStrategy:
        now = datetime.now(UTC)
        sid = f"strategy_{secrets.token_hex(12)}"
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO identity_strategies (id, name, strategy_type, config, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (sid, name, strategy_type, config, now.isoformat(), now.isoformat()),
            )
            await db.commit()
        return IdentityStrategy(
            id=sid, name=name, strategy_type=strategy_type,
            config=config, is_active=True, created_at=now, updated_at=now,
        )

    async def get_strategy(self, strategy_id: str) -> IdentityStrategy | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, name, strategy_type, config, is_active, created_at, updated_at "
                "FROM identity_strategies WHERE id = ?",
                (strategy_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            return IdentityStrategy(
                id=row[0], name=row[1], strategy_type=row[2],
                config=row[3], is_active=bool(row[4]),
                created_at=_parse_dt(row[5]), updated_at=_parse_dt(row[6]),
            )

    async def list_strategies(self, limit: int = 50, offset: int = 0) -> tuple[list[IdentityStrategy], int]:
        async with self._conn() as db:
            async with db.execute("SELECT COUNT(*) FROM identity_strategies") as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                "SELECT id, name, strategy_type, config, is_active, created_at, updated_at "
                "FROM identity_strategies ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
            strategies = [
                IdentityStrategy(
                    id=r[0], name=r[1], strategy_type=r[2],
                    config=r[3], is_active=bool(r[4]),
                    created_at=_parse_dt(r[5]), updated_at=_parse_dt(r[6]),
                )
                for r in rows
            ]
            return strategies, total

    async def update_strategy(
        self,
        strategy_id: str,
        *,
        name: str | None = None,
        config: str | None = None,
        is_active: bool | None = None,
    ) -> IdentityStrategy | None:
        """更新策略字段，返回更新后的策略；不存在返回 None."""
        existing = await self.get_strategy(strategy_id)
        if existing is None:
            return None

        new_name = name if name is not None else existing.name
        new_config = config if config is not None else existing.config
        new_active = is_active if is_active is not None else existing.is_active
        now = datetime.now(UTC)

        async with self._conn() as db:
            await db.execute(
                "UPDATE identity_strategies SET name=?, config=?, is_active=?, updated_at=? WHERE id=?",
                (new_name, new_config, 1 if new_active else 0, now.isoformat(), strategy_id),
            )
            await db.commit()

        return await self.get_strategy(strategy_id)

    async def delete_strategy(self, strategy_id: str) -> bool:
        """删除策略，返回是否成功."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM identity_strategies WHERE id = ?", (strategy_id,),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v)
