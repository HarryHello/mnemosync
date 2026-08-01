"""SqliteIdentityStore 单元测试.

覆盖:
- Actor CRUD: find_or_create_actor, get_actor, list_actors, find_unique_actor_by_display_name
- UserGroup CRUD: create_group, get_group, list_groups
- Membership CRUD: bind_actor_to_group, unbind_actor_from_group,
  get_effective_user_id, list_actor_groups, list_group_members, list_all_bound_actor_ids
- IdentityStrategy CRUD: create_strategy, get_strategy, list_strategies,
  update_strategy, delete_strategy
- resolve_user_identities: batch resolve actor/group IDs
"""

from __future__ import annotations

import pytest
from src.persistence.identity_store import SqliteIdentityStore

# ---------------------------------------------------------------------------
# Actor CRUD
# ---------------------------------------------------------------------------

class TestActorCRUD:
    @pytest.mark.asyncio
    async def test_find_or_create_creates(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor(
            external_key="qq_12345", frontend="astrbot", display_name="Alice",
        )
        assert actor.external_key == "qq_12345"
        assert actor.frontend == "astrbot"
        assert actor.display_name == "Alice"
        assert actor.id.startswith("actor_")

    @pytest.mark.asyncio
    async def test_find_or_create_returns_existing(self, identity_store: SqliteIdentityStore) -> None:
        a1 = await identity_store.find_or_create_actor("k1", "f1", "Name1")
        a2 = await identity_store.find_or_create_actor("k1", "f1")
        assert a1.id == a2.id

    @pytest.mark.asyncio
    async def test_find_or_create_updates_display_name(self, identity_store: SqliteIdentityStore) -> None:
        await identity_store.find_or_create_actor("k1", "f1", "Old")
        updated = await identity_store.find_or_create_actor("k1", "f1", "New")
        assert updated.display_name == "New"

    @pytest.mark.asyncio
    async def test_get_actor_found(self, identity_store: SqliteIdentityStore) -> None:
        created = await identity_store.find_or_create_actor("k1", "f1", "Bob")
        got = await identity_store.get_actor(created.id)
        assert got is not None
        assert got.display_name == "Bob"

    @pytest.mark.asyncio
    async def test_get_actor_not_found(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.get_actor("actor_nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_actors_pagination(self, identity_store: SqliteIdentityStore) -> None:
        for i in range(5):
            await identity_store.find_or_create_actor(f"k{i}", "f1")

        actors, total = await identity_store.list_actors(limit=2, offset=0)
        assert total == 5
        assert len(actors) == 2

        actors2, total2 = await identity_store.list_actors(limit=2, offset=4)
        assert total2 == 5
        assert len(actors2) == 1

    @pytest.mark.asyncio
    async def test_find_unique_actor_by_display_name(self, identity_store: SqliteIdentityStore) -> None:
        await identity_store.find_or_create_actor("k1", "f1", "Unique")
        found = await identity_store.find_unique_actor_by_display_name("f1", "Unique")
        assert found is not None
        assert found.display_name == "Unique"

    @pytest.mark.asyncio
    async def test_find_unique_actor_not_found(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.find_unique_actor_by_display_name("f1", "nobody") is None

    @pytest.mark.asyncio
    async def test_find_unique_actor_ambiguous(self, identity_store: SqliteIdentityStore) -> None:
        """Two actors with same display_name in same frontend -> returns None."""
        await identity_store.find_or_create_actor("k1", "f1", "Same")
        await identity_store.find_or_create_actor("k2", "f1", "Same")
        found = await identity_store.find_unique_actor_by_display_name("f1", "Same")
        assert found is None

    @pytest.mark.asyncio
    async def test_find_unique_actor_different_frontend(self, identity_store: SqliteIdentityStore) -> None:
        """Same name in different frontends -> unique per frontend."""
        await identity_store.find_or_create_actor("k1", "f1", "Name")
        await identity_store.find_or_create_actor("k2", "f2", "Name")
        found = await identity_store.find_unique_actor_by_display_name("f1", "Name")
        assert found is not None


# ---------------------------------------------------------------------------
# UserGroup CRUD
# ---------------------------------------------------------------------------

class TestUserGroupCRUD:
    @pytest.mark.asyncio
    async def test_create_group(self, identity_store: SqliteIdentityStore) -> None:
        g = await identity_store.create_group("My Group")
        assert g.name == "My Group"
        assert g.id.startswith("group_")

    @pytest.mark.asyncio
    async def test_create_group_no_name(self, identity_store: SqliteIdentityStore) -> None:
        g = await identity_store.create_group()
        assert g.name is None

    @pytest.mark.asyncio
    async def test_get_group(self, identity_store: SqliteIdentityStore) -> None:
        g = await identity_store.create_group("Test")
        got = await identity_store.get_group(g.id)
        assert got is not None
        assert got.name == "Test"

    @pytest.mark.asyncio
    async def test_get_group_missing(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.get_group("group_nope") is None

    @pytest.mark.asyncio
    async def test_list_groups(self, identity_store: SqliteIdentityStore) -> None:
        await identity_store.create_group("G1")
        await identity_store.create_group("G2")
        groups, total = await identity_store.list_groups()
        assert total == 2
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# Membership CRUD
# ---------------------------------------------------------------------------

class TestMembershipCRUD:
    @pytest.mark.asyncio
    async def test_bind_and_get_effective_user_id(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor("k1", "f1")
        group = await identity_store.create_group("G1")
        assert await identity_store.bind_actor_to_group(actor.id, group.id) is True
        effective = await identity_store.get_effective_user_id(actor.id)
        assert effective == group.id

    @pytest.mark.asyncio
    async def test_bind_duplicate_returns_false(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor("k1", "f1")
        group = await identity_store.create_group("G1")
        await identity_store.bind_actor_to_group(actor.id, group.id)
        assert await identity_store.bind_actor_to_group(actor.id, group.id) is False

    @pytest.mark.asyncio
    async def test_unbind(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor("k1", "f1")
        group = await identity_store.create_group("G1")
        await identity_store.bind_actor_to_group(actor.id, group.id)
        assert await identity_store.unbind_actor_from_group(actor.id, group.id) is True
        effective = await identity_store.get_effective_user_id(actor.id)
        assert effective == actor.id  # falls back to actor_id

    @pytest.mark.asyncio
    async def test_unbind_nonexistent_returns_false(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.unbind_actor_from_group("a1", "g1") is False

    @pytest.mark.asyncio
    async def test_get_effective_user_id_no_membership(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor("k1", "f1")
        effective = await identity_store.get_effective_user_id(actor.id)
        assert effective == actor.id

    @pytest.mark.asyncio
    async def test_list_actor_groups(self, identity_store: SqliteIdentityStore) -> None:
        actor = await identity_store.find_or_create_actor("k1", "f1")
        g1 = await identity_store.create_group("G1")
        g2 = await identity_store.create_group("G2")
        await identity_store.bind_actor_to_group(actor.id, g1.id)
        await identity_store.bind_actor_to_group(actor.id, g2.id)
        groups = await identity_store.list_actor_groups(actor.id)
        assert len(groups) == 2
        names = {g.name for g in groups}
        assert names == {"G1", "G2"}

    @pytest.mark.asyncio
    async def test_list_group_members(self, identity_store: SqliteIdentityStore) -> None:
        a1 = await identity_store.find_or_create_actor("k1", "f1", "Alice")
        a2 = await identity_store.find_or_create_actor("k2", "f1", "Bob")
        g = await identity_store.create_group("G")
        await identity_store.bind_actor_to_group(a1.id, g.id)
        await identity_store.bind_actor_to_group(a2.id, g.id)
        members = await identity_store.list_group_members(g.id)
        assert len(members) == 2
        names = {m.display_name for m in members}
        assert names == {"Alice", "Bob"}

    @pytest.mark.asyncio
    async def test_list_all_bound_actor_ids(self, identity_store: SqliteIdentityStore) -> None:
        a = await identity_store.find_or_create_actor("k1", "f1")
        g = await identity_store.create_group("G")
        await identity_store.bind_actor_to_group(a.id, g.id)
        pairs = await identity_store.list_all_bound_actor_ids()
        assert len(pairs) == 1
        assert pairs[0] == (a.id, g.id)


# ---------------------------------------------------------------------------
# IdentityStrategy CRUD
# ---------------------------------------------------------------------------

class TestIdentityStrategyCRUD:
    @pytest.mark.asyncio
    async def test_create_strategy(self, identity_store: SqliteIdentityStore) -> None:
        s = await identity_store.create_strategy("direct", "direct", '{"field": "user"}')
        assert s.name == "direct"
        assert s.strategy_type == "direct"
        assert s.is_active is True
        assert s.id.startswith("strategy_")

    @pytest.mark.asyncio
    async def test_get_strategy(self, identity_store: SqliteIdentityStore) -> None:
        created = await identity_store.create_strategy("s1", "regex")
        got = await identity_store.get_strategy(created.id)
        assert got is not None
        assert got.name == "s1"

    @pytest.mark.asyncio
    async def test_get_strategy_missing(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.get_strategy("strategy_nope") is None

    @pytest.mark.asyncio
    async def test_list_strategies(self, identity_store: SqliteIdentityStore) -> None:
        await identity_store.create_strategy("s1", "direct")
        await identity_store.create_strategy("s2", "regex")
        items, total = await identity_store.list_strategies()
        assert total == 2

    @pytest.mark.asyncio
    async def test_update_strategy(self, identity_store: SqliteIdentityStore) -> None:
        s = await identity_store.create_strategy("old", "direct")
        updated = await identity_store.update_strategy(s.id, name="new", is_active=False)
        assert updated is not None
        assert updated.name == "new"
        assert updated.is_active is False

    @pytest.mark.asyncio
    async def test_update_strategy_partial(self, identity_store: SqliteIdentityStore) -> None:
        s = await identity_store.create_strategy("keep", "direct", '{"a":1}')
        updated = await identity_store.update_strategy(s.id, name="changed")
        assert updated is not None
        assert updated.name == "changed"
        assert updated.config == '{"a":1}'

    @pytest.mark.asyncio
    async def test_update_strategy_missing(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.update_strategy("strategy_nope", name="x") is None

    @pytest.mark.asyncio
    async def test_delete_strategy(self, identity_store: SqliteIdentityStore) -> None:
        s = await identity_store.create_strategy("to_delete", "direct")
        assert await identity_store.delete_strategy(s.id) is True
        assert await identity_store.get_strategy(s.id) is None

    @pytest.mark.asyncio
    async def test_delete_strategy_missing(self, identity_store: SqliteIdentityStore) -> None:
        assert await identity_store.delete_strategy("strategy_nope") is False


# ---------------------------------------------------------------------------
# resolve_user_identities
# ---------------------------------------------------------------------------

class TestResolveUserIdentities:
    @pytest.mark.asyncio
    async def test_resolve_actors(self, identity_store: SqliteIdentityStore) -> None:
        a = await identity_store.find_or_create_actor("k1", "f1", "Alice")
        result = await identity_store.resolve_user_identities([a.id])
        assert a.id in result
        group, actors = result[a.id]
        assert group is None
        assert len(actors) == 1
        assert actors[0].display_name == "Alice"

    @pytest.mark.asyncio
    async def test_resolve_groups(self, identity_store: SqliteIdentityStore) -> None:
        g = await identity_store.create_group("MyGroup")
        a1 = await identity_store.find_or_create_actor("k1", "f1", "A")
        await identity_store.bind_actor_to_group(a1.id, g.id)

        result = await identity_store.resolve_user_identities([g.id])
        assert g.id in result
        group, actors = result[g.id]
        assert group is not None
        assert group.name == "MyGroup"
        assert len(actors) == 1

    @pytest.mark.asyncio
    async def test_resolve_empty_list(self, identity_store: SqliteIdentityStore) -> None:
        result = await identity_store.resolve_user_identities([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_resolve_unknown_ids_skipped(self, identity_store: SqliteIdentityStore) -> None:
        result = await identity_store.resolve_user_identities(["actor_unknown", "group_unknown"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_resolve_deduplicates(self, identity_store: SqliteIdentityStore) -> None:
        a = await identity_store.find_or_create_actor("k1", "f1")
        result = await identity_store.resolve_user_identities([a.id, a.id, a.id])
        assert len(result) == 1
