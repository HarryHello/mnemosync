"""AstrBot 插件多模态 content 适配测试 (v0.4.1-beta.15).

回归: 带图请求的 content 是 OpenAI content-part 数组 (text + image_url),
插件曾直接把 list 喂给正则 → TypeError → 整个请求 500.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from plugins.astrbot import AstrBotPlugin


@pytest.fixture
def plugin() -> AstrBotPlugin:
    return AstrBotPlugin()


def test_content_to_text_str_passthrough(plugin: AstrBotPlugin) -> None:
    assert plugin._content_to_text("你好") == "你好"
    assert plugin._content_to_text("") == ""


def test_content_to_text_extracts_text_parts(plugin: AstrBotPlugin) -> None:
    """多模态数组只取 text 部分, 忽略 image_url."""
    content = [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}},
    ]
    assert plugin._content_to_text(content) == "看这张图"


def test_content_to_text_joins_multiple_text_parts(plugin: AstrBotPlugin) -> None:
    content = [
        {"type": "text", "text": "第一段"},
        {"type": "image_url", "image_url": {"url": "https://x/img.png"}},
        {"type": "text", "text": "第二段"},
    ]
    assert plugin._content_to_text(content) == "第一段\n第二段"


def test_content_to_text_fallbacks(plugin: AstrBotPlugin) -> None:
    assert plugin._content_to_text(None) == ""
    assert plugin._content_to_text(123) == "123"
    assert plugin._content_to_text([{"type": "image_url", "image_url": {"url": "x"}}]) == ""


async def test_preprocess_multimodal_message_no_crash(plugin: AstrBotPlugin) -> None:
    """带图消息走完 preprocess 不抛异常, 且 model_messages 保留原始多模态结构."""
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "历史消息"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}},
            ],
        },
    ]
    # 全部 None 的身份上下文 (非归属模式)
    identity = SimpleNamespace(
        actor_id=None, actor=None, frontend="astrbot", external_key=None,
        display_name=None, space_id=None, channel_type=None,
        strategy_name=None, external_event_id=None, effective_user_id=None,
    )
    result = await plugin.preprocess(messages, {}, store=None, identity=identity)  # type: ignore[arg-type]
    # 编译后的当前消息 = 身份标签文本 + 原样保留的图片部分
    content = result.model_messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "<current_speaker" in content[0]["text"]
    assert "看这张图" in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/gif;base64,R0lGOD"},
    }
    # 事件流里的 current 文本不混入图片标记
    current_events = [e for e in result.events if e.origin == "current"]
    assert current_events and current_events[0].content == "看这张图"


async def test_preprocess_pure_image_message_not_dropped(plugin: AstrBotPlugin) -> None:
    """回归 (beta.17): 单发一张图 (无文本) 时消息曾被整体丢弃.

    主对话请求只剩 system, 身份/记忆全部悬空. 修复: 合成 [图片] 占位,
    编译消息保留图片部分.
    """
    messages = [
        {"role": "system", "content": "system prompt"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}},
            ],
        },
    ]
    identity = SimpleNamespace(
        actor_id=None, actor=None, frontend="astrbot", external_key=None,
        display_name=None, space_id=None, channel_type=None,
        strategy_name=None, external_event_id=None, effective_user_id=None,
    )
    result = await plugin.preprocess(messages, {}, store=None, identity=identity)  # type: ignore[arg-type]

    # 当前消息不再被丢弃: 编译文本含 [图片] 占位, 图片部分原样保留
    content = result.model_messages[-1]["content"]
    assert isinstance(content, list)
    assert "<current_speaker" in content[0]["text"]
    assert "[图片]" in content[0]["text"]
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}}

    # current 事件存在 (身份/记忆链路有输入)
    current_events = [e for e in result.events if e.origin == "current"]
    assert current_events and current_events[0].content == "[图片]"


def test_media_placeholder_labels(plugin: AstrBotPlugin) -> None:
    parts = [
        {"type": "image_url", "image_url": {}},
        {"type": "input_audio", "data": "x"},
        {"type": "mystery", "x": 1},
    ]
    assert plugin._media_placeholder(parts) == "[图片] [语音] [媒体]"


def test_last_user_message_handles_list_content() -> None:
    """回归 (beta.17): last_user_message 曾对 list content 返回空串.

    连锁后果: 非流式/流式短期装填以它决定是否追加当前消息,
    带图请求被整体丢弃 → 上游只剩 system, 情绪/检索全跳过.
    """
    from src.core.utils import last_user_message

    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": [{"type": "text", "text": "嗯 图确实是糊的"}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[图片]"},
                {"type": "text", "text": "[Image Attachment: path x.gif]"},
                {"type": "text", "text": "<system_reminder>User ID: 1</system_reminder>"},
                {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}},
            ],
        },
    ]
    text = last_user_message(messages)
    assert "[图片]" in text
    assert "[Image Attachment: path x.gif]" in text
    assert "User ID: 1" in text  # reminder 也属于文本, 由上层清洗处理

    # 纯字符串 content 行为不变
    assert last_user_message([{"role": "user", "content": "hi"}]) == "hi"
    # 无 user 消息返回空
    assert last_user_message([{"role": "assistant", "content": "a"}]) == ""


async def test_extract_multimodal_array_content(plugin: AstrBotPlugin) -> None:
    """回归 (beta.18): AstrBot 始终发数组型 content, extract 曾把原始 list
    直接喂给正则 → TypeError → 非归属模式 (身份 unknown、短期记忆、
    关系/记忆分析全部跳过)。修复: extract 也走 _content_to_text 归一化.
    """
    actor = SimpleNamespace(id="a1")

    class FakeStore:
        async def find_or_create_actor(self, external_key: str, frontend: str, display_name: str):
            assert external_key == "486394990"
            assert frontend == "astrbot"
            return actor

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "早上好"},
                {"type": "text", "text": "<system_reminder>User ID: 486394990, Nickname: 马达\nCurrent datetime: 2026-09-15 11:18 (CST)</system_reminder>"},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[图片]"},
                {"type": "text", "text": "[Image Attachment: path x.gif]"},
                {"type": "text", "text": "<system_reminder>User ID: 486394990, Nickname: 马达\nCurrent datetime: 2026-09-15 11:28 (CST)</system_reminder>"},
                {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}},
            ],
        },
    ]
    result = await plugin.extract(messages, {}, FakeStore())  # type: ignore[arg-type]
    assert result is not None
    assert result.external_key == "486394990"
    assert result.display_name == "马达"
    assert result.channel_type == "direct"
    assert result.metadata["actor_id"] == "a1"


def test_last_user_message_item_returns_full_dict() -> None:
    from src.core.utils import last_user_message_item

    item = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
    messages = [
        {"role": "assistant", "content": "a"},
        item,
        {"role": "assistant", "content": "b"},
    ]
    assert last_user_message_item(messages) is item
    assert last_user_message_item([{"role": "assistant", "content": "a"}]) is None
