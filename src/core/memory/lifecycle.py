"""记忆生命周期管理.

协调 memory_store / vector_store / forwarder 完成记忆的创建、入库、衰减更新.
记忆分析 Agent 产出 CandidateMemory 后, 由本模块负责持久化.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.config import get_settings
from src.core.memory.models import (
    CandidateMemory,
    DecayEvaluation,
    DecayState,
    MemoryEntry,
    MemoryType,
    Relationship,
)
from src.infra.forwarder.multi import MultiForwarder

if TYPE_CHECKING:
    from src.infra.vector_store import VectorStore
    from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)


class MemoryLifecycle:
    """记忆生命周期服务.

    负责:
    - 将 CandidateMemory 转为 MemoryEntry 并入库（SQLite + ChromaDB）
    - 应用衰减评估结果（更新 priority/is_forgotten）
    - 永久记忆限额检查与覆盖
    - 关系状态更新
    """

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        vector_store: "VectorStore",
        forwarder: MultiForwarder,
    ):
        self.memory_store = memory_store
        self.vector_store = vector_store
        self.forwarder = forwarder

    async def store_candidate(
        self,
        candidate: CandidateMemory,
        source_user: str,
    ) -> MemoryEntry | None:
        """将一条候选记忆转为 MemoryEntry 并入库.

        处理永久记忆限额: 若超出, 尝试覆盖 candidate.overrides 指定的记忆.
        若无 overrides 且超限, 降级为普通记忆.

        Returns:
            入库的 MemoryEntry, 若失败返回 None
        """
        settings = get_settings()

        # 永久记忆限额检查
        memory_type = candidate.memory_type
        if memory_type == MemoryType.PERMANENT:
            count = await self.memory_store.count_permanent(source_user)
            if count >= settings.memory.permanent_limit:
                if candidate.overrides:
                    # 覆盖指定记忆
                    await self._delete_memory(candidate.overrides)
                else:
                    # 无明确覆盖目标, 降级为普通记忆
                    logger.warning(
                        "永久记忆已满（%d/%d）且未指定 overrides, 降级为普通记忆: %s",
                        count, settings.memory.permanent_limit, candidate.content[:30],
                    )
                    memory_type = MemoryType.NORMAL

        # 创建 MemoryEntry
        entry = MemoryEntry.create(
            content=candidate.content,
            role=candidate.role,
            source_user=source_user,
            memory_type=memory_type,
            importance=candidate.importance,
            decay_rate=candidate.decay_rate,
        )
        entry.emotional_tags = candidate.emotional_tags
        entry.expires_at = candidate.expires_at
        entry.related_memories = candidate.related_to

        # 生成 embedding 并入库
        try:
            vecs = await self.forwarder.embed(entry.content)
        except Exception as e:
            logger.error("生成 embedding 失败, 记忆未入库: %s", e)
            return None

        try:
            await self.memory_store.save(entry)
            self.vector_store.add(entry, vecs[0])
        except Exception as e:
            logger.error("记忆入库失败: %s", e)
            return None

        # 处理 overrides（如果覆盖的是永久记忆, 现在新记忆已入库, 旧的需要删除）
        # 注意: 上面已经 _delete_memory 了, 这里不重复

        logger.info(
            "记忆入库: id=%s type=%s importance=%.2f content=%s",
            entry.id, entry.memory_type.value, entry.importance, entry.content[:40],
        )
        return entry

    async def apply_decay_evaluations(
        self, evaluations: list[DecayEvaluation]
    ) -> int:
        """应用衰减评估结果, 更新记忆优先级.

        Returns:
            成功更新的条数
        """
        count = 0
        for ev in evaluations:
            try:
                is_forgotten = ev.decision == DecayState.FORGOTTEN
                await self.memory_store.update_priority(
                    ev.memory_id, ev.new_priority, is_forgotten
                )
                # 遗忘的记忆从向量库移除（不删 SQLite, 搜索可恢复）
                if is_forgotten:
                    self.vector_store.delete(ev.memory_id)
                count += 1
            except Exception as e:
                logger.error("应用衰减评估失败 mem=%s: %s", ev.memory_id, e)
        return count

    async def apply_relationship_update(
        self,
        persona_id: str,
        user_id: str,
        intimacy_delta: float,
        trust_delta: float,
        new_type: str | None,
        notes: str | None,
    ) -> Relationship:
        """应用关系分析 Agent 的结果."""
        rel = await self.memory_store.get_relationship(persona_id, user_id)
        if rel is None:
            rel = Relationship.create(persona_id, user_id)
        rel.apply_delta(intimacy_delta, trust_delta, new_type=new_type, notes=notes)
        await self.memory_store.save_relationship(rel)
        return rel

    async def mark_memories_accessed(self, memory_ids: list[str]) -> None:
        """标记被检索加载的记忆为已访问（access_count + 1）."""
        for mid in memory_ids:
            try:
                await self.memory_store.mark_accessed(mid)
            except Exception as e:
                logger.error("标记访问失败 mem=%s: %s", mid, e)

    async def _delete_memory(self, memory_id: str) -> None:
        """删除一条记忆（SQLite + ChromaDB）."""
        try:
            await self.memory_store.delete(memory_id)
        except Exception as e:
            logger.error("SQLite 删除记忆失败 %s: %s", memory_id, e)
        try:
            self.vector_store.delete(memory_id)
        except Exception as e:
            logger.error("ChromaDB 删除记忆失败 %s: %s", memory_id, e)
