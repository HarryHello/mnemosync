"""记忆重建 + 清理 (v0.2.4).

**重建 (reindex)**: 遍历 SQLite 全部记忆, 用当前嵌入模型重新生成向量, 覆盖 ChromaDB.
仅在换嵌入模型后执行 (vector_store 锁定会阻止跨模型直接写入).

**清理 (prune)**: 按纯本地规则删记忆 (SQLite + ChromaDB):
- PERMANENT 永不删
- is_forgotten
- expires_at 已过期
- compute_theoretical_priority() < threshold (默认 0.05)

两者可组合: reindex 时可选顺便 prune, 也可独立触发 prune。

**并发/锁定**: `ReindexProgress` 是进程内单例, 状态转换用 `asyncio.Lock` 保护;
`state=='running'` 时 `MemoryLifecycle.store_candidate` 会检查并拒写 (避免边重建
边写入导致语义混杂). 进程重启后状态复位为 idle (故意, 不需持久化).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from src.core.memory.models import MemoryEntry, MemoryType
from src.infra.llm_service.models import ModelType

if TYPE_CHECKING:
    from src.core.models.resolver import RoleResolver
    from src.infra.forwarder.multi import MultiForwarder
    from src.infra.vector_store import VectorStore
    from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)


class ReindexState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ReindexProgress:
    """进程内单例, 记录 reindex 进度. 通过 app.state 暴露."""

    state: ReindexState = ReindexState.IDLE
    total: int = 0
    processed: int = 0
    pruned: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_running(self) -> bool:
        return self.state == ReindexState.RUNNING

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "total": self.total,
            "processed": self.processed,
            "pruned": self.pruned,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


def should_prune(
    entry: MemoryEntry,
    *,
    now: datetime | None = None,
    priority_threshold: float = 0.05,
) -> tuple[bool, str]:
    """纯本地判定. PERMANENT 永远保留.

    Returns:
        (should_delete, reason). reason ∈ {'', 'forgotten', 'expired', 'low_priority'}
    """
    if entry.memory_type == MemoryType.PERMANENT:
        return False, ""
    if entry.is_forgotten:
        return True, "forgotten"
    now = now or datetime.now(UTC)
    if entry.expires_at is not None and entry.expires_at < now:
        return True, "expired"
    if entry.compute_theoretical_priority() < priority_threshold:
        return True, "low_priority"
    return False, ""


@dataclass
class PruneBreakdown:
    forgotten: int = 0
    expired: int = 0
    low_priority: int = 0

    def total(self) -> int:
        return self.forgotten + self.expired + self.low_priority

    def as_dict(self) -> dict:
        return {
            "forgotten": self.forgotten,
            "expired": self.expired,
            "low_priority": self.low_priority,
        }


@dataclass
class PruneResult:
    total_before: int
    would_delete: int
    deleted: int
    breakdown: PruneBreakdown


class Reindexer:
    """重建执行器. 单次 run() 全流程."""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        vector_store: VectorStore,
        forwarder: MultiForwarder,
        resolver: RoleResolver,
        progress: ReindexProgress,
    ):
        self.memory_store = memory_store
        self.vector_store = vector_store
        self.forwarder = forwarder
        self.resolver = resolver
        self.progress = progress

    async def run(
        self,
        *,
        prune: bool = False,
        priority_threshold: float = 0.05,
        batch_size: int = 50,
    ) -> None:
        """执行 reindex. 拒绝并发 (progress.lock).

        Steps:
          1. 抢锁 → 转 running, 清计数
          2. 解析嵌入候选; 无则报错
          3. reset_collection() + clear_embedding_lock()
          4. 遍历 memory_store.iter_all(), 逐条:
             a) prune=True 且命中规则 → 删 SQLite, 计入 pruned
             b) 否则 embed + vector_store.add
          5. lock_embedding()
          6. state=success, finished_at
        """
        # 抢锁 (并发保护)
        if self.progress.lock.locked():
            raise RuntimeError("reindex 已在运行中")
        async with self.progress.lock:
            self._reset_progress()
            try:
                cand = await self.resolver.first(ModelType.EMBEDDING)
            except Exception as e:
                self._fail(f"无可用嵌入模型: {e}")
                raise

            self.progress.state = ReindexState.RUNNING
            self.progress.started_at = datetime.now(UTC)
            logger.info(
                "reindex 开始: prune=%s threshold=%.3f embedding=%s/%s",
                prune, priority_threshold, cand.service_id, cand.model,
            )

            try:
                self.progress.total = await self.memory_store.count_all()
                self.vector_store.reset_collection()

                now = datetime.now(UTC)
                batch: list[tuple[MemoryEntry, list[float]]] = []
                async for entry in self.memory_store.iter_all(batch_size=batch_size):
                    if prune:
                        drop, _reason = should_prune(
                            entry, now=now, priority_threshold=priority_threshold
                        )
                        if drop:
                            await self.memory_store.delete(entry.id)
                            self.progress.pruned += 1
                            self.progress.processed += 1
                            continue
                    # 生成新向量
                    vecs = await self.forwarder.embed(entry.content)
                    batch.append((entry, vecs[0]))
                    if len(batch) >= batch_size:
                        self._flush_batch(batch, cand)
                        batch = []
                    self.progress.processed += 1
                if batch:
                    self._flush_batch(batch, cand)

                # 全部完成后设锁 (即使 collection 为空, 便于后续 assert)
                # 用第一条向量的实际维度; 若空 collection, 用 candidate.embedding_dim 或探测
                lock_dim = cand.embedding_dim
                if lock_dim is None:
                    # 空库无历史向量 → 探测一次
                    probe = await self.forwarder.embed("hi")
                    lock_dim = len(probe[0])
                self.vector_store.lock_embedding(cand.service_id, cand.model, lock_dim)

                self.progress.state = ReindexState.SUCCESS
                self.progress.finished_at = datetime.now(UTC)
                logger.info(
                    "reindex 完成: total=%d processed=%d pruned=%d",
                    self.progress.total, self.progress.processed, self.progress.pruned,
                )
            except Exception as e:
                logger.exception("reindex 失败")
                self._fail(str(e))
                raise

    def _flush_batch(self, batch, cand):
        for entry, vec in batch:
            self.vector_store.add(entry, vec)

    def _reset_progress(self) -> None:
        self.progress.state = ReindexState.IDLE
        self.progress.total = 0
        self.progress.processed = 0
        self.progress.pruned = 0
        self.progress.started_at = None
        self.progress.finished_at = None
        self.progress.error = None

    def _fail(self, error: str) -> None:
        self.progress.state = ReindexState.ERROR
        self.progress.error = error
        self.progress.finished_at = datetime.now(UTC)


class Pruner:
    """独立清理执行器. 不动向量的嵌入锁, 仅按规则删条目."""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        vector_store: VectorStore,
    ):
        self.memory_store = memory_store
        self.vector_store = vector_store

    async def run(
        self,
        *,
        priority_threshold: float = 0.05,
        dry_run: bool = False,
        batch_size: int = 200,
    ) -> PruneResult:
        total_before = await self.memory_store.count_all()
        breakdown = PruneBreakdown()
        to_delete: list[str] = []
        now = datetime.now(UTC)

        async for entry in self.memory_store.iter_all(batch_size=batch_size):
            drop, reason = should_prune(
                entry, now=now, priority_threshold=priority_threshold
            )
            if not drop:
                continue
            to_delete.append(entry.id)
            if reason == "forgotten":
                breakdown.forgotten += 1
            elif reason == "expired":
                breakdown.expired += 1
            elif reason == "low_priority":
                breakdown.low_priority += 1

        if dry_run:
            return PruneResult(
                total_before=total_before,
                would_delete=len(to_delete),
                deleted=0,
                breakdown=breakdown,
            )

        deleted = 0
        for mid in to_delete:
            try:
                await self.memory_store.delete(mid)
                self.vector_store.delete(mid)
                deleted += 1
            except Exception as e:
                logger.error("prune 删除失败 %s: %s", mid, e)
        return PruneResult(
            total_before=total_before,
            would_delete=len(to_delete),
            deleted=deleted,
            breakdown=breakdown,
        )


__all__ = [
    "ReindexState",
    "ReindexProgress",
    "Reindexer",
    "Pruner",
    "PruneBreakdown",
    "PruneResult",
    "should_prune",
]
