"""Pytest 顶层 conftest.

- 保证 tests 能 import src.* (从项目根目录跑 pytest 就无需 sys.path 调整,
  但通过 conftest 显式声明更稳)
- 提供 reset_settings 钩子, 让依赖 Settings 单例的用例互不污染
- 提供跨模块共享 fixtures: in-memory SQLite stores, mock forwarder/vector_store
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.core.config import _reset_settings


@pytest.fixture(autouse=True)
def _reset_settings_singleton():
    """每个用例前后重置 Settings 单例, 防止跨用例污染."""
    _reset_settings()
    yield
    _reset_settings()


# ---------------------------------------------------------------------------
# Shared fixtures for persistence stores (in-memory SQLite)
# ---------------------------------------------------------------------------

@pytest.fixture
async def auth_store():
    """In-memory SqliteAuthStore for testing."""
    from src.persistence.auth_store import SqliteAuthStore

    store = SqliteAuthStore(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
async def identity_store():
    """In-memory SqliteIdentityStore for testing."""
    from src.persistence.identity_store import SqliteIdentityStore

    store = SqliteIdentityStore(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
async def memory_store():
    """In-memory SqliteMemoryStore for testing."""
    from src.persistence.memory_store import SqliteMemoryStore

    store = SqliteMemoryStore(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
async def relationship_store():
    """In-memory SqliteRelationshipStore for testing."""
    from src.persistence.relationship_store import SqliteRelationshipStore

    store = SqliteRelationshipStore(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
async def notification_store():
    """In-memory NotificationStore for testing."""
    from src.persistence.notification_store import NotificationStore

    store = NotificationStore(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def mock_vector_store():
    """Mock VectorStore that accepts any add/delete call."""
    vs = MagicMock()
    vs.add = MagicMock()
    vs.delete = MagicMock()
    vs.assert_embedding_matches = MagicMock()
    return vs


@pytest.fixture
def mock_multi_forwarder():
    """Mock MultiForwarder that returns deterministic embed results."""
    mf = AsyncMock()
    mf.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    mf.chat = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    return mf


@pytest.fixture
def mock_role_resolver():
    """Mock RoleResolver."""
    from src.infra.llm_service.models import ModelType, ResolvedCandidate

    resolver = AsyncMock()
    candidate = ResolvedCandidate(
        role=ModelType.EMBEDDING,
        priority=1,
        service_id="test_service",
        base_url="http://localhost",
        api_key="test-key",
        model="test-model",
        embedding_dim=384,
        send_dimensions=False,
    )
    resolver.first = AsyncMock(return_value=candidate)
    resolver.for_role = AsyncMock(return_value=[candidate])
    return resolver
