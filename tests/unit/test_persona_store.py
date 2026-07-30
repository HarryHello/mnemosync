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
        ),
    )
    await store.save(defn, changelog="初始版本")
    active = await store.get_active()
    assert active is not None
    assert active.name == "绫音"
    assert active.identity.personality == "你叫绫音, 是一个沉默寡言的高中生。"
    assert active.identity.speaking_style == "冷淡、短句、少用语气词"
    assert "重视家人" in active.identity.values

    # verify to_legacy_prompt builds correctly (no context in prompt)
    prompt = active.to_legacy_prompt()
    assert "人格设定" in prompt
    assert "说话风格" in prompt
    assert "核心价值" in prompt
    assert "背景设定" not in prompt  # context removed, per-user field


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
            persona_addressing="绫音",
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
    assert identity.persona_addressing == "绫音"

    # With space override
    identity = defn.get_identity_for_space("space-A")
    assert identity.speaking_style == "在A群的说话风格"
    assert identity.persona_addressing == "绫音"  # not overridden


@pytest.mark.asyncio
async def test_legacy_migration(store):
    legacy = PersonaDefinition.from_legacy(
        name="绫音",
        prompt="你叫绫音, 是高中生。",
        persona_addressing="绫音",
    )
    assert legacy.version == "0.0.0"
    assert legacy.identity.personality == "你叫绫音, 是高中生。"
    assert legacy.identity.persona_addressing == "绫音"

    prompt = legacy.to_legacy_prompt()
    assert "人格设定" in prompt
    assert "你叫绫音" in prompt


@pytest.mark.asyncio
async def test_multi_persona_profiles(store):
    """验证多人格 profile 的基本 CRUD."""
    # 创建人格 A
    pid_a = await store.create_persona(name="人格A", description="测试A")
    profile_a = await store.get_persona(pid_a)
    assert profile_a is not None
    assert profile_a["name"] == "人格A"
    assert not profile_a["is_active"]  # 默认不激活

    # 创建人格 B
    pid_b = await store.create_persona(name="人格B")
    assert await store.get_persona(pid_b) is not None

    # 激活 A
    ok = await store.activate_persona(pid_a)
    assert ok
    active = await store.get_active_persona()
    assert active is not None
    assert active["id"] == pid_a

    # 切换到 B
    ok = await store.activate_persona(pid_b)
    assert ok
    active = await store.get_active_persona()
    assert active["id"] == pid_b

    # 列出所有人格 (含 init_db 时迁移创建的默认人格)
    all_p = await store.list_personas()
    assert len(all_p) == 3

    # 更新人格 B 的名称
    ok = await store.update_persona(pid_b, name="人格B-改")
    assert ok
    updated = await store.get_persona(pid_b)
    assert updated["name"] == "人格B-改"

    # 删除人格 A
    ok = await store.delete_persona(pid_a)
    assert ok
    assert await store.get_persona(pid_a) is None
    assert await store.count() == 0  # persona_versions were also deleted


@pytest.mark.asyncio
async def test_save_with_persona_id(store):
    """确认 save 时关联到正确的人格 profile."""
    pid = await store.create_persona(name="测试人格")

    defn = PersonaDefinition(
        version="1.0.0", name="测试人格",
        identity=PersonaIdentity(personality="hello"),
    )
    await store.save(defn, persona_id=pid, changelog="first")

    # get_active 应能跨 profile 关联找到
    active = await store.get_active()
    assert active is not None
    assert active.identity.personality == "hello"

    # 保存到另一个 profile
    pid2 = await store.create_persona(name="另一个人格")
    defn2 = PersonaDefinition(
        version="1.0.0", name="另一个人格",
        identity=PersonaIdentity(personality="world"),
    )
    await store.save(defn2, persona_id=pid2, changelog="first")

    # 切换 profile 后应读到不同的定义
    await store.activate_persona(pid2)
    active2 = await store.get_active()
    assert active2.identity.personality == "world"


@pytest.mark.asyncio
async def test_backward_compat_from_dict(store):
    """旧 JSON 中包含 user_addressing/context 应被静默忽略."""
    old_json = {
        "version": "1.0.0",
        "name": "绫音",
        "identity": {
            "personality": "测试",
            "speaking_style": "冷淡",
            "values": ["诚实"],
            "persona_addressing": "绫音",
            "user_addressing": "哥哥",
            "context": "同住",
        },
        "space_overrides": {},
        "author": None,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defn = PersonaDefinition.from_dict(old_json)
    assert defn.identity.personality == "测试"
    assert defn.identity.persona_addressing == "绫音"
    # 旧字段被静默忽略
    assert not hasattr(defn.identity, "user_addressing")
    assert not hasattr(defn.identity, "context")
