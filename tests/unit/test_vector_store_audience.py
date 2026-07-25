"""VectorStore 受众粗筛 (v0.3.0 Sub-Phase C) 测试.

验证 search() 的复合 where 子句 ($or) 在真实 ChromaDB 上的行为:
粗筛返回"可能可见"的超集, 绝不漏掉可见记忆 (宁可多召, 精筛再淘汰)。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.memory.audience import AudienceFilter, RetrievalContext
from src.core.memory.models import MemoryEntry, Visibility
from src.infra.vector_store import VectorStore


def _entry(
    idx: str,
    source_user: str,
    visibility: Visibility = Visibility.SOURCE_RESTRICTED,
    space_id: str | None = None,
    vector: list[float] | None = None,
) -> MemoryEntry:
    e = MemoryEntry(
        id=f"m-{idx}",
        content=f"content {idx}",
        role="user",
        source_user=source_user,
        visibility=visibility,
        space_id=space_id,
        created_at=datetime.now(timezone.utc),
    )
    return e


@pytest.fixture
def store(tmp_path):
    vs = VectorStore(str(tmp_path / "chroma"), collection_name="test_audience")
    now = datetime.now(timezone.utc)
    # 所有向量几乎相同 → 粗筛结果只由 where 决定
    vec = [0.1, 0.2, 0.3]
    vs.add(_entry("own", "bob"), vec)
    vs.add(_entry("other-private", "alice"), vec)
    vs.add(_entry("public", "alice", visibility=Visibility.PUBLIC), vec)
    vs.add(_entry("space-shared", "alice", visibility=Visibility.FRIENDS_ONLY, space_id="g1"), vec)
    vs.add(_entry("other-space", "alice", visibility=Visibility.PUBLIC, space_id="g2"), vec)
    return vs


def _ids(results: list[dict]) -> set[str]:
    return {r["id"] for r in results}


def test_legacy_source_user_filter_still_works(store) -> None:
    """v0.2.x 路径: source_user 精确过滤不受影响."""
    results = store.search([0.1, 0.2, 0.3], top_k=20, source_user="bob")
    assert _ids(results) == {"m-own"}


def test_where_private_chat_superset(store) -> None:
    """私聊粗筛: 自己桶 + PUBLIC (空间共享的 FRIENDS_ONLY 不召回)."""
    ctx = RetrievalContext(effective_user_id="bob", space_id=None)
    where = AudienceFilter.build_chromadb_where(ctx)
    results = store.search([0.1, 0.2, 0.3], top_k=20, where=where)
    ids = _ids(results)
    assert "m-own" in ids
    assert "m-public" in ids
    assert "m-other-private" not in ids
    assert "m-space-shared" not in ids  # FRIENDS_ONLY + 无 space 条件


def test_where_group_chat_includes_space(store) -> None:
    """群聊粗筛: 自己 + public + 本空间."""
    ctx = RetrievalContext(effective_user_id="bob", space_id="g1")
    where = AudienceFilter.build_chromadb_where(ctx)
    results = store.search([0.1, 0.2, 0.3], top_k=20, where=where)
    ids = _ids(results)
    assert "m-own" in ids
    assert "m-public" in ids
    assert "m-space-shared" in ids       # 本空间共享
    assert "m-other-private" not in ids  # 别人的私有绝不召回


def test_where_unattributed_public_only(store) -> None:
    ctx = RetrievalContext(effective_user_id=None)
    where = AudienceFilter.build_chromadb_where(ctx)
    results = store.search([0.1, 0.2, 0.3], top_k=20, where=where)
    ids = _ids(results)
    assert ids == {"m-public", "m-other-space"}  # 只有 PUBLIC 两条


def test_space_id_in_metadata(store) -> None:
    """space_id 写入 ChromaDB metadata, 供 where 过滤."""
    results = store.search(
        [0.1, 0.2, 0.3], top_k=20, where={"space_id": "g1"},
    )
    ids = _ids(results)
    assert ids == {"m-space-shared"}
