"""旧 api_key_bound 策略 → 内建「Key 即身份」迁移测试."""

from __future__ import annotations

import pytest
from src.persistence.api_key_store import ApiKey, SqliteApiKeyStore
from src.persistence.identity_migration import migrate_legacy_api_key_bound
from src.persistence.identity_store import SqliteIdentityStore


@pytest.fixture
async def api_key_store(tmp_path):
    store = SqliteApiKeyStore(str(tmp_path / "api_keys.db"))
    await store.init_db()
    return store


@pytest.mark.asyncio
async def test_migrate_legacy_api_key_bound(
    identity_store: SqliteIdentityStore,
    api_key_store: SqliteApiKeyStore,
) -> None:
    """旧手动 api_key_bound 策略下的 Key 升级为内建标记, 策略记录被删除."""
    # 建一个旧的手动 api_key_bound 策略
    legacy = await identity_store.create_strategy(
        name="旧Key即身份", strategy_type="api_key_bound",
        config='{"external_key": "api-key-bound", "frontend": "chatbox"}',
    )
    # 两个 Key 绑定它
    key_a = ApiKey.generate(note="Cherry", strategy_id=legacy.id)
    key_b = ApiKey.generate(note="bot", strategy_id=legacy.id)
    await api_key_store.save(key_a)
    await api_key_store.save(key_b)

    migrated = await migrate_legacy_api_key_bound(identity_store, api_key_store)

    assert migrated == 2
    # 策略记录被删除
    assert await identity_store.get_strategy(legacy.id) is None
    # Key 的 strategy_id 改为内建标记
    for k in (key_a, key_b):
        got = await api_key_store.get_by_id(k.id)
        assert got is not None
        assert got.strategy_id == "api_key_bound"


@pytest.mark.asyncio
async def test_migrate_idempotent(
    identity_store: SqliteIdentityStore,
    api_key_store: SqliteApiKeyStore,
) -> None:
    """没有旧策略时迁移无事发生; 二次运行也不再迁移."""
    assert await migrate_legacy_api_key_bound(identity_store, api_key_store) == 0
    assert await migrate_legacy_api_key_bound(identity_store, api_key_store) == 0


@pytest.mark.asyncio
async def test_migrate_skips_other_strategy_types(
    identity_store: SqliteIdentityStore,
    api_key_store: SqliteApiKeyStore,
) -> None:
    """非 api_key_bound 策略不受影响."""
    other = await identity_store.create_strategy(
        name="QQ正则", strategy_type="regex",
        config='{"actor_pattern": "\\\\d+"}',
    )
    key = ApiKey.generate(note="qq", strategy_id=other.id)
    await api_key_store.save(key)

    assert await migrate_legacy_api_key_bound(identity_store, api_key_store) == 0
    got = await api_key_store.get_by_id(key.id)
    assert got is not None
    assert got.strategy_id == other.id
    assert await identity_store.get_strategy(other.id) is not None
