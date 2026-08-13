"""提示词清洗模块缓存 + 跳过配置存储.

两张表 (单库 data/prompt_cache.db):
- prompt_cache: 按 (frontend, module_hash) 缓存模块清洗结果
- prompt_clean_settings: 按 (frontend, module_title) 配置「跳过不清洗」模块

设计 (v0.4.1):
- 缓存按前台 (api_key.note) 隔离 + `*` 通配跳过配置
- 上限 10000 条, 写时按 updated_at LRU 裁剪最旧
- 手动编辑直接覆盖 clean_prompt (无手动标记), 删缓存即下次自动重洗
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.persistence.base import SqliteStore

MAX_CACHE_ENTRIES = 10000

# `*` 通配前台键: 命中所有前台 (全局跳过配置)
WILDCARD_FRONTEND = "*"


@dataclass
class PromptCacheEntry:
    frontend: str
    module_hash: str
    module_title: str
    module_text: str
    clean_prompt: str
    skipped: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class PromptCleanSetting:
    frontend: str
    module_title: str
    skip: bool = False


class PromptCacheStore(SqliteStore):
    """提示词清洗模块缓存 + 跳过配置存储."""

    _enable_foreign_keys = False

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_cache (
                frontend TEXT NOT NULL,
                module_hash TEXT NOT NULL,
                module_title TEXT NOT NULL,
                module_text TEXT NOT NULL,
                clean_prompt TEXT NOT NULL DEFAULT '',
                skipped INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (frontend, module_hash)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_clean_settings (
                frontend TEXT NOT NULL,
                module_title TEXT NOT NULL,
                skip INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (frontend, module_title)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_cache_updated ON prompt_cache(updated_at)"
        )

    # ── 缓存 CRUD ──────────────────────────────────────────────

    async def get(self, frontend: str, module_hash: str) -> PromptCacheEntry | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT frontend, module_hash, module_title, module_text, clean_prompt, "
                "skipped, created_at, updated_at FROM prompt_cache "
                "WHERE frontend = ? AND module_hash = ?",
                (frontend, module_hash),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return PromptCacheEntry(
            frontend=row[0], module_hash=row[1], module_title=row[2],
            module_text=row[3], clean_prompt=row[4], skipped=bool(row[5]),
            created_at=row[6], updated_at=row[7],
        )

    async def save(self, entry: PromptCacheEntry) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO prompt_cache "
                "(frontend, module_hash, module_title, module_text, clean_prompt, "
                " skipped, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.frontend, entry.module_hash, entry.module_title,
                    entry.module_text, entry.clean_prompt,
                    1 if entry.skipped else 0,
                    entry.created_at, now,
                ),
            )
            await self._trim(db)
            await db.commit()

    async def update_clean_prompt(
        self, frontend: str, module_hash: str, clean_prompt: str,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE prompt_cache SET clean_prompt = ?, updated_at = ? "
                "WHERE frontend = ? AND module_hash = ?",
                (clean_prompt, now, frontend, module_hash),
            )
            await db.commit()
            return cur.rowcount > 0

    async def delete(self, frontend: str, module_hash: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM prompt_cache WHERE frontend = ? AND module_hash = ?",
                (frontend, module_hash),
            )
            await db.commit()
            return cur.rowcount > 0

    async def clear(self) -> int:
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM prompt_cache")
            await db.commit()
            return cur.rowcount

    async def list_cache(
        self, limit: int = 50, offset: int = 0, frontend: str | None = None,
    ) -> tuple[list[PromptCacheEntry], int]:
        where = "WHERE frontend = ?" if frontend else ""
        params: tuple[Any, ...] = (frontend,) if frontend else ()
        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM prompt_cache {where}", params,
            ) as cur:
                total_row = await cur.fetchone()
            total = int(total_row[0]) if total_row else 0
            async with db.execute(
                f"SELECT frontend, module_hash, module_title, module_text, clean_prompt, "
                f"skipped, created_at, updated_at FROM prompt_cache {where} "
                f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        items = [
            PromptCacheEntry(
                frontend=r[0], module_hash=r[1], module_title=r[2],
                module_text=r[3], clean_prompt=r[4], skipped=bool(r[5]),
                created_at=r[6], updated_at=r[7],
            )
            for r in rows
        ]
        return items, total

    async def _trim(self, db: aiosqlite.Connection) -> None:
        """超过上限时按 updated_at LRU 裁剪最旧 (SQLite 隐式 rowid).

        用 LIMIT 短路快速判断 (仅在疑似超限时才取 COUNT), 避免每次写入全表 COUNT.
        """
        cur = await db.execute(
            f"SELECT rowid FROM prompt_cache LIMIT {MAX_CACHE_ENTRIES + 1}"
        )
        over_limit_rows = list(await cur.fetchall())
        if len(over_limit_rows) <= MAX_CACHE_ENTRIES:
            return
        async with db.execute("SELECT COUNT(*) FROM prompt_cache") as cnt:
            total_row = await cnt.fetchone()
        total = int(total_row[0]) if total_row else 0
        overflow = max(total - MAX_CACHE_ENTRIES, 1)
        await db.execute(
            "DELETE FROM prompt_cache WHERE rowid IN ("
            " SELECT rowid FROM prompt_cache ORDER BY updated_at ASC LIMIT ?)",
            (overflow,),
        )

    # ── 跳过配置 ───────────────────────────────────────────────

    async def skip_modules(self, frontend: str) -> set[str]:
        """返回前台命中的跳过模块标题集 (含 `*` 通配)."""
        async with self._conn() as db:
            async with db.execute(
                "SELECT module_title FROM prompt_clean_settings WHERE skip = 1 "
                "AND frontend IN (?, ?)",
                (frontend, WILDCARD_FRONTEND),
            ) as cur:
                rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def set_skip(self, frontend: str, module_title: str, skip: bool) -> None:
        async with self._conn() as db:
            if skip:
                await db.execute(
                    "INSERT OR REPLACE INTO prompt_clean_settings "
                    "(frontend, module_title, skip) VALUES (?, ?, 1)",
                    (frontend, module_title),
                )
            else:
                await db.execute(
                    "DELETE FROM prompt_clean_settings "
                    "WHERE frontend = ? AND module_title = ?",
                    (frontend, module_title),
                )
            await db.commit()

    async def list_settings(self) -> list[PromptCleanSetting]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT frontend, module_title, skip FROM prompt_clean_settings "
                "ORDER BY frontend, module_title",
            ) as cur:
                rows = await cur.fetchall()
        return [
            PromptCleanSetting(frontend=r[0], module_title=r[1], skip=bool(r[2]))
            for r in rows
        ]
