"""结构化人格存储测试."""

import pytest

from src.core.persona.definition import PersonaDefinition, PersonaIdentity, PersonaOverride
from src.persistence.persona_store import SqlitePersonaStore


@pytest.fixture
async def store(tmp_path):
    s = SqlitePersonaStore(str(tmp_path / "identity.db"))
    await s.init_db()
    return s


@pytest.mark.asyncio
async def test_save_and_get_active(store):
    defn = PersonaDefinition(
        version="1.0.0",
        name="绫音",
        identity=PersonaIdentity(
            personality="你叫绫音, 是一个沉默寡言的高中生。",
            speaking_style="冷淡、短句、少用语气词",
            values=["重视家人", "不喜欢说谎"],
            persona_addressing="绫音",
            user_addressing="哥哥",
            context="和哥哥住在同一个公寓。",
        ),
    )
    await store.save(defn, changelog="初始版本")
    active = await store.get_active()
    assert active is not None
    assert active.name == "绫音"
    assert active.identity.personality == "你叫绫音, 是一个沉默寡言的高中生。"
    assert active.identity.speaking_style == "冷淡、短句、少用语气词"
    assert "重视家人" in active.identity.values

    # verify to_legacy_prompt builds correctly
    prompt = active.to_legacy_prompt()
    assert "人格设定" in prompt
    assert "说话风格" in prompt
    assert "核心价值" in prompt
    assert "背景设定" in prompt


@pytest.mark.asyncio
async def test_version_list_and_count(store):
    defn = PersonaDefinition(version="1.0.0", name="绫音", identity=PersonaIdentity(personality="v1"))
    await store.save(defn, changelog="v1")

    defn2 = PersonaDefinition(version="1.0.1", name="绫音", identity=PersonaIdentity(personality="v2"))
    await store.save(defn2, changelog="v2")

    assert await store.count() == 2
    versions = await store.list_versions()
    assert len(versions) == 2
    # latest should be active
    assert versions[0]["active"]  # newest first
    assert not versions[1]["active"]


@pytest.mark.asyncio
async def test_rollback(store):
    defn = PersonaDefinition(version="1.0.0", name="绫音", identity=PersonaIdentity(personality="original"))
    await store.save(defn, changelog="original")
    original_id = 1  # first row

    defn2 = PersonaDefinition(version="2.0.0", name="绫音", identity=PersonaIdentity(personality="updated"))
    await store.save(defn2, changelog="update")

    active = await store.get_active()
    assert active.identity.personality == "updated"

    ok = await store.rollback(original_id)
    assert ok

    active = await store.get_active()
    assert active.identity.personality == "original"


@pytest.mark.asyncio
async def test_get_identity_for_space(store):
    defn = PersonaDefinition(
        version="1.0.0",
        name="绫音",
        identity=PersonaIdentity(
            speaking_style="默认说话风格",
            context="默认背景",
        ),
        space_overrides={
            "space-A": PersonaOverride(
                speaking_style="在A群的说话风格",
            ),
        },
    )
    # Without space override
    identity = defn.get_identity_for_space(None)
    assert identity.speaking_style == "默认说话风格"
    assert identity.context == "默认背景"

    # With space override
    identity = defn.get_identity_for_space("space-A")
    assert identity.speaking_style == "在A群的说话风格"
    assert identity.context == "默认背景"  # not overridden


@pytest.mark.asyncio
async def test_legacy_migration(store):
    legacy = PersonaDefinition.from_legacy(
        name="绫音",
        prompt="你叫绫音, 是高中生。",
        persona_addressing="绫音",
        user_addressing="哥哥",
        context="同住公寓",
    )
    assert legacy.version == "0.0.0"
    assert legacy.identity.personality == "你叫绫音, 是高中生。"
    assert legacy.identity.persona_addressing == "绫音"
    assert legacy.identity.user_addressing == "哥哥"
    assert legacy.identity.context == "同住公寓"

    prompt = legacy.to_legacy_prompt()
    assert "人格设定" in prompt
    assert "你叫绫音" in prompt
