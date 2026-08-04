"""通知中心存储 (v0.2.13).

通用面板通知表, 覆盖记忆入库失败等异步事件——用户可能不盯着调试面板,
但依然需要知道 "过去某段时间有 N 条记忆没入库"。

结构: 单表 append + read_at 时间戳 (NULL=未读)。level / category 都是自由字符串,
新事件类型不需要 schema 变更。meta_json 存结构化附加字段 (content 预览、
upstream_status 等), UI 按 category 决定是否展开。

生命周期:
  * 后端各处捕获异常时 add() 一条
  * 面板拉列表 / 未读数 / mark_read / mark_all_read / delete
  * (未来可加) 数量或年龄超阈值时自动裁旧
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.persistence.base import SqliteStore


@dataclass
class Notification:
    """一条通知记录."""

    id: int | None
    created_at: datetime
    level: str  # "info" | "warning" | "error"
    category: str  # 自由字符串, 如 "memory_write_failed"
    title: str
    message: str
    meta: dict[str, Any] | None
    read_at: datetime | None


class NotificationStore(SqliteStore):
    """通知的 SQLite 存储 (append + 标记已读 + 删除)."""

    _enable_foreign_keys = False

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP NOT NULL,
                level TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                meta_json TEXT,
                read_at TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_created_at "
            "ON notifications(created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_unread "
            "ON notifications(read_at) WHERE read_at IS NULL"
        )

    async def init_db(self) -> None:
        async with self._conn() as db:
            await self._init_schema(db)
            await db.commit()

    async def add(
        self,
        *,
        level: str,
        category: str,
        title: str,
        message: str,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """追加一条通知, 返回 rowid."""
        if level not in ("info", "warning", "error"):
            raise ValueError(f"invalid level: {level!r}")
        if not category or not title:
            raise ValueError("category / title must be non-empty")
        stamp = datetime.now(UTC).isoformat()
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
        async with self._conn() as db:
            cur = await db.execute(
                "INSERT INTO notifications "
                "(created_at, level, category, title, message, meta_json, read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (stamp, level, category, title, message, meta_json),
            )
            await db.commit()
            return cur.lastrowid or 0

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        """按 created_at 降序分页, 返回 (当前页, 匹配总数)."""
        where_sql = " WHERE read_at IS NULL" if unread_only else ""
        async with self._conn() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM notifications{where_sql}"
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0
            async with db.execute(
                f"SELECT id, created_at, level, category, title, message, "
                f"meta_json, read_at FROM notifications{where_sql} "
                f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
                items = [self._row_to_notification(r) for r in rows]
        return items, total

    async def count_unread(self) -> int:
        async with self._conn() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def mark_read(self, notification_id: int) -> bool:
        """标记单条已读, 返回是否命中未读记录."""
        stamp = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE notifications SET read_at = ? "
                "WHERE id = ? AND read_at IS NULL",
                (stamp, notification_id),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def mark_all_read(self) -> int:
        """标记全部已读, 返回受影响条数."""
        stamp = datetime.now(UTC).isoformat()
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE notifications SET read_at = ? WHERE read_at IS NULL",
                (stamp,),
            )
            await db.commit()
            return cur.rowcount or 0

    async def delete_by_id(self, notification_id: int) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM notifications WHERE id = ?",
                (notification_id,),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def delete_read(self) -> int:
        """清空所有已读通知, 返回被删条数."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM notifications WHERE read_at IS NOT NULL")
            await db.commit()
            return cur.rowcount or 0

    async def get(self, notification_id: int) -> Notification | None:
        async with self._conn() as db:
            async with db.execute(
                "SELECT id, created_at, level, category, title, message, "
                "meta_json, read_at FROM notifications WHERE id = ?",
                (notification_id,),
            ) as cur:
                row = await cur.fetchone()
                return self._row_to_notification(row) if row else None

    @staticmethod
    def _row_to_notification(row: Sequence[Any]) -> Notification:
        created = datetime.fromisoformat(row[1]) if row[1] else datetime.now(UTC)
        read = datetime.fromisoformat(row[7]) if row[7] else None
        meta_json = row[6]
        meta: dict[str, Any] | None = None
        if meta_json:
            try:
                parsed = json.loads(meta_json)
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = None
        return Notification(
            id=row[0],
            created_at=created,
            level=row[2],
            category=row[3],
            title=row[4],
            message=row[5],
            meta=meta,
            read_at=read,
        )
