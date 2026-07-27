"""AstrBot 插件逐说话者规范化测试."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from src.core.identity.models import IdentityContext
from src.persistence.identity_store import SqliteIdentityStore


@pytest.fixture
async def identity_store(tmp_path: Path):
    store = SqliteIdentityStore(str(tmp_path / "identity.db"))
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def plugin():
    path = Path(__file__).parents[2] / "plugins" / "astrbot.py"
    spec = importlib.util.spec_from_file_location("test_astrbot_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AstrBotPlugin()


@pytest.mark.asyncio
async def test_preprocess_splits_group_context_by_speaker(plugin, identity_store) -> None:
    current = await identity_store.find_or_create_actor("486394990", "astrbot", "马达")
    other = await identity_store.find_or_create_actor("1914089741", "astrbot", "Harry")
    identity = IdentityContext(
        actor_id=current.id,
        actor=current,
        frontend="astrbot",
        external_key=current.external_key,
        display_name=current.display_name,
        space_id="测试群",
        channel_type="group",
        strategy_name="AstrBot",
        effective_user_id=current.id,
    )
    content = """你好
<system_reminder>User ID: 486394990, Nickname: 马达
Group name: 测试群
Current datetime: 2026-07-26 23:42:00 (CST)</system_reminder>
<system_reminder>You are in a group chat. Belows are group chat context:
--- BEGIN CONTEXT---
[Harry/23:40:01]: 晚上好
[马达/23:41:02]: 我来了
--- END CONTEXT ---
</system_reminder>"""

    result = await plugin.preprocess(
        [{"role": "user", "content": content}], {}, identity_store, identity
    )

    assert [event.origin for event in result.events] == [
        "history_snapshot", "history_snapshot", "current",
    ]
    assert result.events[0].actor_id == other.id
    assert result.events[0].external_key == "1914089741"
    assert result.events[1].actor_id == current.id
    assert result.events[2].content == "你好"
    assert result.events[2].actor_id == current.id
    model_content = result.model_messages[0]["content"]
    assert '<current_speaker identity="马达 | QQ 486394990">' in model_content
    assert "你好" in model_content


@pytest.mark.asyncio
async def test_unknown_history_speaker_is_not_current_actor(plugin, identity_store) -> None:
    current = await identity_store.find_or_create_actor("486394990", "astrbot", "马达")
    identity = IdentityContext(
        actor_id=current.id,
        actor=current,
        frontend="astrbot",
        external_key=current.external_key,
        display_name=current.display_name,
        space_id="测试群",
        channel_type="group",
        strategy_name="AstrBot",
        effective_user_id=current.id,
    )
    content = """当前消息
<system_reminder>User ID: 486394990, Nickname: 马达
Group name: 测试群
Current datetime: 2026-07-26 23:42:00 (CST)</system_reminder>
<system_reminder>You are in a group chat. Belows are group chat context:
--- BEGIN CONTEXT---
[完全未知的人/23:40:01]: 不要错误归属
--- END CONTEXT ---
</system_reminder>"""

    result = await plugin.preprocess(
        [{"role": "user", "content": content}], {}, identity_store, identity
    )

    history = result.events[0]
    assert history.display_name == "完全未知的人"
    assert history.actor_id is None
    assert history.effective_user_id is None
    assert result.current_event is not None
    assert result.current_event.actor_id == current.id


@pytest.mark.asyncio
async def test_current_minute_timestamp_follows_same_minute_history(
    plugin, identity_store,
) -> None:
    """Current datetime 只有分钟精度时，当前消息不能倒排到同分钟历史之前."""
    current = await identity_store.find_or_create_actor("486394990", "astrbot", "马达")
    identity = IdentityContext(
        actor_id=current.id,
        actor=current,
        frontend="astrbot",
        external_key=current.external_key,
        display_name=current.display_name,
        space_id="测试群",
        channel_type="group",
        strategy_name="AstrBot",
        effective_user_id=current.id,
    )
    content = """晚安
<system_reminder>User ID: 486394990, Nickname: 马达
Group name: 测试群
Current datetime: 2026-07-26 22:04 (CST)</system_reminder>
<system_reminder>You are in a group chat. Belows are group chat context:
--- BEGIN CONTEXT---
[Harry/22:04:32]: 晚上好
[马达/22:04:43]: 晚上好
--- END CONTEXT ---
</system_reminder>"""

    result = await plugin.preprocess(
        [{"role": "user", "content": content}], {}, identity_store, identity
    )

    assert result.current_event is not None
    history_times = [event.source_timestamp for event in result.events[:-1]]
    assert all(timestamp is not None for timestamp in history_times)
    assert result.current_event.source_timestamp > max(history_times)
