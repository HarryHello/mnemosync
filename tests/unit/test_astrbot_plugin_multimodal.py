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
