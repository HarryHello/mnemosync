"""空间社交策略存储测试."""

import pytest
from src.persistence.space_policy_store import SpacePolicy, SqliteSpacePolicyStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteSpacePolicyStore(str(tmp_path / "identity.db"))
    await s.init_db()
    return s


@pytest.mark.asyncio
async def test_save_and_get(store):
    policy = SpacePolicy(
        space_id="space-A",
        expressor_enabled=True,
        expressor_temperature=0.5,
        preferred_max_length=100,
        use_emojis=False,
    )
    await store.upsert(policy)

    loaded = await store.get("space-A")
    assert loaded is not None
    assert loaded.space_id == "space-A"
    assert loaded.expressor_enabled is True
    assert loaded.expressor_temperature == 0.5
    assert loaded.preferred_max_length == 100
    assert loaded.use_emojis is False


@pytest.mark.asyncio
async def test_get_nonexistent(store):
    loaded = await store.get("nonexistent")
    assert loaded is None


@pytest.mark.asyncio
async def test_upsert_overwrite(store):
    policy = SpacePolicy(space_id="space-A", expressor_temperature=0.3)
    await store.upsert(policy)
    loaded = await store.get("space-A")
    assert loaded.expressor_temperature == 0.3

    policy.expressor_temperature = 0.7
    await store.upsert(policy)
    loaded = await store.get("space-A")
    assert loaded.expressor_temperature == 0.7


@pytest.mark.asyncio
async def test_delete(store):
    policy = SpacePolicy(space_id="space-A")
    await store.upsert(policy)
    assert await store.delete("space-A") is True
    assert await store.delete("space-A") is False
    assert await store.get("space-A") is None


@pytest.mark.asyncio
async def test_list_all(store):
    for sid in ["space-A", "space-B", "space-C"]:
        await store.upsert(SpacePolicy(space_id=sid))

    policies = await store.list_all()
    assert len(policies) == 3
    sids = [p.space_id for p in policies]
    assert "space-A" in sids
    assert "space-B" in sids
    assert "space-C" in sids


@pytest.mark.asyncio
async def test_default_values(store):
    policy = SpacePolicy(space_id="space-A")
    assert policy.expressor_enabled is True
    assert policy.expressor_temperature == 0.4
    assert policy.preferred_max_length == 200
    assert policy.use_emojis is True
