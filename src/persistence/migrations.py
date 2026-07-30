"""SQLite 迁移运行器.

替代手写的 try/except ALTER TABLE 模式. 每个 store 定义命名迁移列表,
MigrationRunner 在 _init_schema 中幂等执行未应用的迁移, 记录到 _migrations 表.

用法::

    class MyStore(SqliteStore):
        _MIGRATIONS: list[tuple[str, Callable]] = [
            ("001_add_column_x", add_column_if_missing("my_table", "x", "TEXT")),
            ("002_add_column_y", _migrate_data_y),
        ]

        @staticmethod
        async def _init_schema(db: aiosqlite.Connection) -> None:
            await db.execute("CREATE TABLE IF NOT EXISTS ...")
            await MigrationRunner(MyStore._MIGRATIONS).apply(db)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aiosqlite

logger = logging.getLogger(__name__)

# 一个迁移函数: 接收 db connection, 执行 DDL/DML
MigrationFn = Callable[[aiosqlite.Connection], Awaitable[None]]


def add_column_if_missing(table: str, column: str, col_type: str) -> MigrationFn:
    """创建一个幂等迁移函数: ALTER TABLE ADD COLUMN, 列已存在时静默跳过."""
    ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"

    async def _migrate(db: aiosqlite.Connection) -> None:
        try:
            await db.execute(ddl)
        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e):
                return
            raise

    return _migrate


class MigrationRunner:
    """幂等执行命名迁移, 已应用的迁移记录到 _migrations 表."""

    def __init__(self, migrations: list[tuple[str, MigrationFn]]) -> None:
        self._migrations = migrations

    async def apply(self, db: aiosqlite.Connection) -> None:
        """确保 _migrations 表存在, 执行所有未应用的迁移 (按顺序)."""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        async with db.execute("SELECT name FROM _migrations") as cur:
            applied = {row[0] async for row in cur}

        for name, fn in self._migrations:
            if name in applied:
                continue
            logger.info("Running migration: %s", name)
            try:
                await fn(db)
                await db.execute(
                    "INSERT INTO _migrations (name) VALUES (?)", (name,)
                )
                await db.commit()
            except Exception as e:
                logger.error("Migration %s failed: %s", name, e)
                await db.rollback()
                raise
