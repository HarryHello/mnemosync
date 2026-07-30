"""身份解析器 (v0.3.0) 测试.

覆盖:
  * 无策略 → 非归属 (effective_user_id=None)
  * direct 策略: request.user → Actor → effective_user_id
  * api_key_bound 策略: 固定身份
  * regex 策略: 从消息内容提取 actor / space / event_id (AstrBot 风格)
  * regex 未命中 → 非归属
  * UserGroup 绑定后 effective_user_id 收敛到 group_id
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.identity.resolver import IdentityResolver
from src.persistence.identity_store import SqliteIdentityStore


@pytest.fixture
async def store(tmp_path: Path) -> SqliteIdentityStore:
    s = SqliteIdentityStore(str(tmp_path / "identity.db"))
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_no_strategy_unattributed(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    ctx = await resolver.resolve(
        request_user="someone",
        messages=[{"role": "user", "content": "hi"}],
        strategy_config=None,
        strategy_type=None,
        strategy_name=None,
    )
    assert ctx.actor_id is None
    assert ctx.effective_user_id is None  # 非归属: 不映射到任何用户桶
    assert ctx.strategy_name is None


@pytest.mark.asyncio
async def test_direct_strategy_uses_request_user(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    ctx = await resolver.resolve(
        request_user="alice",
        messages=[],
        strategy_config={"frontend": "web"},
        strategy_type="direct",
        strategy_name="web-direct",
    )
    assert ctx.actor_id is not None
    assert ctx.external_key == "alice"
    assert ctx.frontend == "web"
    # 未绑定 UserGroup → effective_user_id = actor_id
    assert ctx.effective_user_id == ctx.actor_id
    assert ctx.strategy_name == "web-direct"


@pytest.mark.asyncio
async def test_direct_strategy_without_user_unattributed(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    ctx = await resolver.resolve(
        request_user=None,
        messages=[],
        strategy_config={"frontend": "web"},
        strategy_type="direct",
        strategy_name=None,
    )
    assert ctx.actor_id is None
    assert ctx.effective_user_id is None


@pytest.mark.asyncio
async def test_api_key_bound_strategy(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    config = {
        "external_key": "local-user",
        "frontend": "chatbox",
        "display_name": "本地用户",
        "channel_type": "direct",
    }
    ctx1 = await resolver.resolve(
        request_user=None, messages=[],
        strategy_config=config, strategy_type="api_key_bound", strategy_name="chatbox",
    )
    ctx2 = await resolver.resolve(
        request_user=None, messages=[],
        strategy_config=config, strategy_type="api_key_bound", strategy_name="chatbox",
    )
    assert ctx1.actor_id is not None
    assert ctx1.external_key == "local-user"
    assert ctx1.display_name == "本地用户"
    # 同一固定身份 → 同一 Actor (幂等)
    assert ctx1.actor_id == ctx2.actor_id


@pytest.mark.asyncio
async def test_regex_strategy_extracts_actor_space_event(store: SqliteIdentityStore) -> None:
    """AstrBot 风格: 身份信息塞在 system 消息文本里."""
    resolver = IdentityResolver(store)
    config = {
        "actor_pattern": r"QQ号[:：]\s*(\d+)",
        "name_pattern": r"用户名[:：]\s*(\S+)",
        "space_pattern": r"群号[:：]\s*(\d+)",
        "event_id_pattern": r"消息ID[:：]\s*(\S+)",
        "search_in": "system_or_first_user",
        "frontend": "astrbot",
    }
    messages = [
        {
            "role": "system",
            "content": "QQ号：12345\n用户名：小明\n群号：67890\n消息ID：msg-001",
        },
        {"role": "user", "content": "大家好"},
    ]
    ctx = await resolver.resolve(
        request_user=None, messages=messages,
        strategy_config=config, strategy_type="regex", strategy_name="astrbot-qq",
    )
    assert ctx.external_key == "12345"
    assert ctx.display_name == "小明"
    assert ctx.space_id == "67890"
    assert ctx.external_event_id == "msg-001"
    assert ctx.channel_type == "group"  # 有 space_id → 群聊
    assert ctx.frontend == "astrbot"


@pytest.mark.asyncio
async def test_regex_strategy_no_match_unattributed(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    ctx = await resolver.resolve(
        request_user=None,
        messages=[{"role": "user", "content": "没有身份信息的普通消息"}],
        strategy_config={"actor_pattern": r"QQ号[:：]\s*(\d+)", "frontend": "astrbot"},
        strategy_type="regex",
        strategy_name=None,
    )
    assert ctx.actor_id is None
    assert ctx.effective_user_id is None


@pytest.mark.asyncio
async def test_regex_direct_channel_without_space(store: SqliteIdentityStore) -> None:
    """regex 只提到 QQ 号没有群号 → 私聊 (channel_type=direct)."""
    resolver = IdentityResolver(store)
    messages = [{"role": "system", "content": "QQ号：999\n用户名：小红"}]
    ctx = await resolver.resolve(
        request_user=None, messages=messages,
        strategy_config={
            "actor_pattern": r"QQ号[:：]\s*(\d+)",
            "name_pattern": r"用户名[:：]\s*(\S+)",
            "frontend": "astrbot",
        },
        strategy_type="regex",
        strategy_name=None,
    )
    assert ctx.external_key == "999"
    assert ctx.space_id is None
    assert ctx.channel_type == "direct"


@pytest.mark.asyncio
async def test_effective_user_id_collapses_to_group(store: SqliteIdentityStore) -> None:
    """同一人的两个 Actor 绑定到 UserGroup 后共享 effective_user_id."""
    resolver = IdentityResolver(store)
    qq = await resolver.resolve(
        request_user="12345", messages=[],
        strategy_config={"frontend": "astrbot"},
        strategy_type="direct", strategy_name=None,
    )
    discord = await resolver.resolve(
        request_user="67890", messages=[],
        strategy_config={"frontend": "maibot"},
        strategy_type="direct", strategy_name=None,
    )
    assert qq.actor_id != discord.actor_id
    # 绑定前: 各自独立
    assert qq.effective_user_id == qq.actor_id
    assert discord.effective_user_id == discord.actor_id

    group = await store.create_group(name="张三")
    await store.bind_actor_to_group(qq.actor_id, group.id)
    await store.bind_actor_to_group(discord.actor_id, group.id)

    qq2 = await resolver.resolve(
        request_user="12345", messages=[],
        strategy_config={"frontend": "astrbot"},
        strategy_type="direct", strategy_name=None,
    )
    discord2 = await resolver.resolve(
        request_user="67890", messages=[],
        strategy_config={"frontend": "maibot"},
        strategy_type="direct", strategy_name=None,
    )
    # 绑定后: 收敛到同一 group_id → 共享记忆与关系
    assert qq2.effective_user_id == group.id
    assert discord2.effective_user_id == group.id


@pytest.mark.asyncio
async def test_unknown_strategy_type_unattributed(store: SqliteIdentityStore) -> None:
    resolver = IdentityResolver(store)
    ctx = await resolver.resolve(
        request_user="x", messages=[],
        strategy_config={}, strategy_type="telepathy", strategy_name=None,
    )
    assert ctx.actor_id is None
