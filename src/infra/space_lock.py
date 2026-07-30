"""空间级串行锁.

同一空间内的请求串行处理: 第二条消息等待第一条处理完毕.
不同空间之间并行, 互不阻塞.

锁键优先级: space_id > source_user > api_key_id > "global".
无 space_id 的私聊按用户隔离, 避免不同用户互相阻塞.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SpaceLockManager:
    """管理 per-key asyncio.Lock 的单例."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, key: str) -> asyncio.Lock:
        """获取 key 对应的锁 (不存在则创建)."""
        # 双检查: 先看已有, 没有再加锁创建
        lock = self._locks.get(key)
        if lock is not None:
            return lock
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def lock_key(
        self,
        *,
        space_id: str | None,
        source_user: str | None,
        api_key_id: str | None,
    ) -> str:
        """派生锁键: space_id > source_user > api_key_id > global."""
        return space_id or source_user or api_key_id or "global"
