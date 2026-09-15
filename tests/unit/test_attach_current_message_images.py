"""attach_current_message_images 单元测试 (v0.4.1-beta.19).

回归: 图片恢复曾按 (role, content 前 100 字符) 在原始消息里找对应条目,
身份插件把当前消息重写为 <current_speaker> 标签文本后必然对不上,
图片被静默丢弃 (beta.18 实测: 上游只剩文本, 模型自述看不到图)。
"""

from __future__ import annotations

from src.api.routes.forward.dispatch import attach_current_message_images

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOD"}}
GIF_TAG = "<current_speaker identity=\"马达\">\n[图片]\n</current_speaker>"


def test_attaches_image_after_plugin_rewrite() -> None:
    """插件重写文本后, 图片 parts 仍能显式接回当前消息."""
    combined = [
        {"role": "system", "content": "人格提示词"},
        {"role": "user", "content": GIF_TAG},
    ]
    client_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[图片]"},
                {"type": "text", "text": "<system_reminder>User ID: 1</system_reminder>"},
                IMAGE,
            ],
        },
    ]
    result = attach_current_message_images(combined, client_messages)
    current = result[-1]
    assert isinstance(current["content"], list)
    assert current["content"][0] == {"type": "text", "text": GIF_TAG}
    assert current["content"][1] == IMAGE


def test_no_image_leaves_messages_untouched() -> None:
    """client 消息无图片时不改写 (内容保持纯字符串)."""
    combined = [{"role": "system", "content": "s"}, {"role": "user", "content": "早上好"}]
    client_messages = [{"role": "user", "content": [{"type": "text", "text": "早上好"}]}]
    result = attach_current_message_images(combined, client_messages)
    assert result == combined


def test_empty_inputs_noop() -> None:
    assert attach_current_message_images([], [{"role": "user", "content": [IMAGE]}]) == []
    assert attach_current_message_images([{"role": "user", "content": "hi"}], []) == [
        {"role": "user", "content": "hi"}
    ]


def test_non_string_current_noop() -> None:
    """当前消息已是数组 (无插件直通场景) 时不重复包装."""
    combined = [{"role": "user", "content": ["text", IMAGE]}]
    result = attach_current_message_images(combined, [{"role": "user", "content": [IMAGE]}])
    assert result == combined


def test_uses_last_client_user_message() -> None:
    """多轮历史里取最后一条 client user 消息的图片 parts."""
    combined = [{"role": "user", "content": "新消息"}]
    client_messages = [
        {"role": "user", "content": ["旧图片文本", IMAGE]},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": [{"type": "text", "text": "本轮新消息"}]},
    ]
    result = attach_current_message_images(combined, client_messages)
    assert result == combined  # 最后一条 user 消息无图片 → 不改写
