"""SQLite Store 基类.

统一长/短连接管理、PRAGMA 配置、生命周期, 消除各 store 中的重复样板代码.

子类只需实现:
  - ``_init_schema(db)``: 静态方法, 幂等建表/迁移
  - 各自的 CRUD 方法
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class SqliteStore:
    """SQLite 存储基类, 提供长连接/短连接双模式和统一 PRAGMA 配置."""

    # 子类可覆盖以控制是否启用 foreign_keys
    _enable_foreign_keys: bool = True

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """建立长连接并初始化 schema (幂等)."""
        if self._db is not None:
            return
        # 确保父目录存在
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        if self._enable_foreign_keys:
            await self._db.execute("PRAGMA foreign_keys=ON")
        await self._init_schema(self._db)
        await self._db.commit()
        await self._post_connect(self._db)

    async def _post_connect(self, db: aiosqlite.Connection) -> None:
        """长连接建立后的钩子. 子类可覆盖以执行数据迁移等."""

    async def close(self) -> None:
        """关闭长连接."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        """内部连接上下文: 长连接模式复用 self._db, 否则临时开连接."""
        if self._db is not None:
            yield self._db
        else:
            async with aiosqlite.connect(self.db_path) as db:
                yield db

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        """幂等建表/迁移. 子类必须覆盖此方法."""
        raise NotImplementedError
