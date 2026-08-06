"""MemoryLifecycle 单元测试.

覆盖:
- store_candidate: 正常入库, reindex 阻止, embedding 失败, 永久记忆限额, overrides
- apply_decay_evaluations: 正常更新, 遗忘删除向量
- apply_relationship_update: 新建/更新关系
- mark_memories_accessed: 批量标记
- _delete_memory: 双存储删除
- _notify_write_failure: 通知中心写入
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from src.core.memory.lifecycle import MemoryLifecycle
from src.core.memory.models import (
    CandidateMemory,
    DecayEvaluation,
    DecayState,
    MemoryEntry,
    MemoryType,
)
from src.infra.forwarder.forwarder import UpstreamError
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.relationship_store import SqliteRelationshipStore

# ---------------------------------------------------------------------------
# store_candidate
# ---------------------------------------------------------------------------

class TestStoreCandidate:
    @pytest.mark.asyncio
    async def test_store_candidate_success(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
        mock_role_resolver,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            resolver=mock_role_resolver,
        )
        candidate = CandidateMemory(
            content="User likes cats",
            role="user",
            memory_type=MemoryType.NORMAL,
            importance=0.8,
            decay_rate=0.3,
            emotional_tags=["happy"],
        )

        entry = await lc.store_candidate(candidate, source_user="user_1")
        assert entry is not None
        assert entry.content == "User likes cats"
        assert entry.source_user == "user_1"
        assert entry.importance == 0.8
        mock_vector_store.add.assert_called_once()
        mock_multi_forwarder.embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_candidate_reindex_blocks_write(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        reindex_progress = MagicMock()
        reindex_progress.is_running.return_value = True

        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            reindex_progress=reindex_progress,
        )
        candidate = CandidateMemory(
            content="test", role="user", memory_type=MemoryType.NORMAL,
            importance=0.5, decay_rate=0.3, emotional_tags=[],
        )

        result = await lc.store_candidate(candidate, source_user="u1")
        assert result is None
        mock_multi_forwarder.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_candidate_embed_failure(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        mock_multi_forwarder.embed.side_effect = UpstreamError(502, "bad gateway")

        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        candidate = CandidateMemory(
            content="test", role="user", memory_type=MemoryType.NORMAL,
            importance=0.5, decay_rate=0.3, emotional_tags=[],
        )

        result = await lc.store_candidate(candidate, source_user="u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_candidate_permanent_overrides_deletes_old(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
        mock_role_resolver,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            resolver=mock_role_resolver,
        )

        # Create a permanent memory first
        old = CandidateMemory(
            content="old permanent", role="user", memory_type=MemoryType.PERMANENT,
            importance=1.0, decay_rate=0.0, emotional_tags=[],
        )
        old_entry = await lc.store_candidate(old, source_user="u1")
        assert old_entry is not None

        # Verify it's stored as permanent
        count = await memory_store.count_permanent("u1")
        assert count >= 1

        # Now store a new permanent that overrides the old one.
        # Patch get_settings at the module level to set permanent_limit to 1.
        import src.core.memory.lifecycle as lc_mod
        original_get_settings = lc_mod.get_settings
        settings_mock = MagicMock()
        settings_mock.memory.permanent_limit = 1
        lc_mod.get_settings = lambda: settings_mock
        try:
            new = CandidateMemory(
                content="new permanent", role="user", memory_type=MemoryType.PERMANENT,
                importance=1.0, decay_rate=0.0, emotional_tags=[], overrides=old_entry.id,
            )
            new_entry = await lc.store_candidate(new, source_user="u1")
        finally:
            lc_mod.get_settings = original_get_settings

        assert new_entry is not None
        assert new_entry.content == "new permanent"
        mock_vector_store.delete.assert_called_with(old_entry.id)

    @pytest.mark.asyncio
    async def test_store_candidate_permanent_no_overrides_downgrades(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
        mock_role_resolver,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            resolver=mock_role_resolver,
        )
        settings_patch = MagicMock()
        settings_patch.memory.permanent_limit = 1

        # Fill permanent quota
        c1 = CandidateMemory(
            content="first", role="user", memory_type=MemoryType.PERMANENT,
            importance=1.0, decay_rate=0.0, emotional_tags=[],
        )
        await lc.store_candidate(c1, source_user="u1")

        # Second permanent without overrides -> should downgrade
        with patch("src.core.memory.lifecycle.get_settings", return_value=settings_patch):
            c2 = CandidateMemory(
                content="second no overrides", role="user", memory_type=MemoryType.PERMANENT,
                importance=1.0, decay_rate=0.0, emotional_tags=[],
            )
            entry = await lc.store_candidate(c2, source_user="u1")
        assert entry is not None
        assert entry.memory_type == MemoryType.NORMAL  # downgraded


# ---------------------------------------------------------------------------
# apply_decay_evaluations
# ---------------------------------------------------------------------------

class TestApplyDecayEvaluations:
    @pytest.mark.asyncio
    async def test_apply_decay_updates_priority(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        # Create a memory entry
        entry = MemoryEntry.create(content="test decay", role="user", importance=0.5)
        await memory_store.save(entry)

        ev = DecayEvaluation(
            memory_id=entry.id,
            current_priority=0.5,
            new_priority=0.2,
            decision=DecayState.DORMANT,
            factors={"time_factor": 0.5},
            reflection="decayed",
        )
        count = await lc.apply_decay_evaluations([ev])
        assert count == 1

    @pytest.mark.asyncio
    async def test_apply_decay_forgotten_removes_from_vector_store(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        entry = MemoryEntry.create(content="forgotten me", role="user", importance=0.1)
        await memory_store.save(entry)

        ev = DecayEvaluation(
            memory_id=entry.id,
            current_priority=0.05,
            new_priority=0.02,
            decision=DecayState.FORGOTTEN,
            factors={},
            reflection="forgotten",
        )
        count = await lc.apply_decay_evaluations([ev])
        assert count == 1
        mock_vector_store.delete.assert_called_with(entry.id)


# ---------------------------------------------------------------------------
# apply_relationship_update
# ---------------------------------------------------------------------------

class TestApplyRelationshipUpdate:
    @pytest.mark.asyncio
    async def test_create_new_relationship(
        self,
        memory_store: SqliteMemoryStore,
        relationship_store: SqliteRelationshipStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            relationship_store=relationship_store,
        )
        rel = await lc.apply_relationship_update(
            persona_id="persona_1", user_id="user_1",
            intimacy_delta=0.3, trust_delta=0.2,
            new_type="acquaintance", notes="first meeting",
        )
        assert rel.persona_id == "persona_1"
        assert rel.user_id == "user_1"
        assert rel.type == "acquaintance"
        assert 0.29 < rel.intimacy_score < 0.31
        assert 0.19 < rel.trust_level < 0.21

    @pytest.mark.asyncio
    async def test_update_existing_relationship(
        self,
        memory_store: SqliteMemoryStore,
        relationship_store: SqliteRelationshipStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            relationship_store=relationship_store,
        )
        await lc.apply_relationship_update(
            "p1", "u1", 0.1, 0.1, "acquaintance", "note1",
        )
        rel = await lc.apply_relationship_update(
            "p1", "u1", 0.2, 0.3, "friend", "note2",
        )
        assert rel.type == "friend"
        assert rel.interaction_count == 2


# ---------------------------------------------------------------------------
# mark_memories_accessed
# ---------------------------------------------------------------------------

class TestMarkMemoriesAccessed:
    @pytest.mark.asyncio
    async def test_marks_accessed(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        e1 = MemoryEntry.create(content="a", role="user")
        e2 = MemoryEntry.create(content="b", role="user")
        await memory_store.save(e1)
        await memory_store.save(e2)

        await lc.mark_memories_accessed([e1.id, e2.id])
        # No assertion on internal state; just verifying no exception
        # (access_count is updated in DB)

    @pytest.mark.asyncio
    async def test_marks_empty_list(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        await lc.mark_memories_accessed([])  # no error


# ---------------------------------------------------------------------------
# _delete_memory
# ---------------------------------------------------------------------------

class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_deletes_from_both_stores(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        entry = MemoryEntry.create(content="delete me", role="user")
        await memory_store.save(entry)
        await lc._delete_memory(entry.id)
        mock_vector_store.delete.assert_called_with(entry.id)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
        )
        await lc._delete_memory("mem_nonexistent")  # no exception


# ---------------------------------------------------------------------------
# _notify_write_failure
# ---------------------------------------------------------------------------

class TestNotifyWriteFailure:
    @pytest.mark.asyncio
    async def test_notify_with_store(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
        notification_store,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            notification_store=notification_store,
        )
        error = UpstreamError(503, "service unavailable")
        await lc._notify_write_failure(
            stage="embed", error=error, content="some content",
        )
        notif, total = await notification_store.list_page(limit=10, offset=0)
        assert total == 1
        assert notif[0].level == "warning"
        assert notif[0].category == "memory_write_failed"

    @pytest.mark.asyncio
    async def test_notify_without_store(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            notification_store=None,
        )
        await lc._notify_write_failure(
            stage="embed", error=RuntimeError("x"), content="y",
        )  # no error

    @pytest.mark.asyncio
    async def test_notify_includes_upstream_status(
        self,
        memory_store: SqliteMemoryStore,
        mock_vector_store,
        mock_multi_forwarder,
        notification_store,
    ) -> None:
        lc = MemoryLifecycle(
            memory_store=memory_store,
            vector_store=mock_vector_store,
            forwarder=mock_multi_forwarder,
            notification_store=notification_store,
        )
        error = UpstreamError(429, "rate limited")
        await lc._notify_write_failure(
            stage="persist", error=error, content="test",
        )
        notif, _ = await notification_store.list_page(limit=1, offset=0)
        assert notif[0].meta["upstream_status"] == 429
        assert notif[0].meta["stage"] == "persist"
