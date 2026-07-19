"""VectorStore 嵌入锁定单元测试.

覆盖:
- 空 collection 未锁定时, assert_embedding_matches 自动 lock
- 已锁定后, 不同 service/model/dim 触发 VectorStoreLockError
- clear_embedding_lock + reset_collection 后可重新锁定
- reset_collection 清空数据但重建 collection 保持 hnsw:space
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.memory.models import MemoryEntry, MemoryType, Visibility
from src.infra.vector_store import VectorStore, VectorStoreLockError


def _make_entry(idx: int = 0) -> MemoryEntry:
    return MemoryEntry(
        id=f"m-{idx}",
        content=f"content {idx}",
        role="user",
        source_user="alice",
        memory_type=MemoryType.NORMAL,
        importance=0.5,
        decay_rate=0.1,
        priority=0.5,
        access_count=0,
        is_forgotten=False,
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
        emotional_tags=[],
        visibility=Visibility.SOURCE_RESTRICTED,
        expires_at=None,
        related_memories=[],
    )


@pytest.fixture
def store(tmp_path):
    return VectorStore(str(tmp_path / "chroma"), collection_name="test_lock")


def test_get_embedding_lock_returns_none_when_unlocked(store):
    assert store.get_embedding_lock() is None


def test_assert_locks_on_first_write(store):
    store.assert_embedding_matches("svc-a", "embed-v3", 1024)
    lock = store.get_embedding_lock()
    assert lock == {"service_id": "svc-a", "model": "embed-v3", "dim": 1024}


def test_assert_passes_when_matches(store):
    store.lock_embedding("svc-a", "embed-v3", 1024)
    store.assert_embedding_matches("svc-a", "embed-v3", 1024)


def test_assert_raises_on_service_mismatch(store):
    store.lock_embedding("svc-a", "embed-v3", 1024)
    with pytest.raises(VectorStoreLockError) as exc_info:
        store.assert_embedding_matches("svc-b", "embed-v3", 1024)
    assert exc_info.value.locked_service_id == "svc-a"
    assert exc_info.value.got_service_id == "svc-b"


def test_assert_raises_on_model_mismatch(store):
    store.lock_embedding("svc-a", "embed-v3", 1024)
    with pytest.raises(VectorStoreLockError):
        store.assert_embedding_matches("svc-a", "embed-v2", 1024)


def test_assert_raises_on_dim_mismatch(store):
    store.lock_embedding("svc-a", "embed-v3", 1024)
    with pytest.raises(VectorStoreLockError):
        store.assert_embedding_matches("svc-a", "embed-v3", 768)


def test_clear_lock_allows_relocking_to_different_model(store):
    store.lock_embedding("svc-a", "embed-v3", 1024)
    store.clear_embedding_lock()
    assert store.get_embedding_lock() is None
    store.assert_embedding_matches("svc-b", "embed-v2", 768)
    lock = store.get_embedding_lock()
    assert lock == {"service_id": "svc-b", "model": "embed-v2", "dim": 768}


def test_reset_collection_wipes_data_and_lock(store):
    store.lock_embedding("svc-a", "embed-v3", 3)
    entry = _make_entry(1)
    store.add(entry, [0.1, 0.2, 0.3])
    assert store.count() == 1
    store.reset_collection()
    assert store.count() == 0
    assert store.get_embedding_lock() is None
