"""触发原因识别.

从客户端请求中判断本次交互的触发上下文（@提及/回复/关键词/常规），
注入主对话 system，让模型理解自己为什么被呼叫、应该回应谁。

不依赖客户端专用适配；仅使用 OpenAI 标准字段和消息中的常见模式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerReason:
    """本轮请求的触发原因."""

    reason: str           # mentioned | reply | keyword | normal
    description: str
    target: str | None = None  # @的目标 / 回复的发言者


def infer_trigger_reason(
    current_speaker: str | None,
    current_content: str,
    channel_type: str | None = None,
    *,
    reply_to: str | None = None,
) -> TriggerReason:
    """从本轮发言内容推断触发原因.

    优先级:
    1. 回复关系 (reply_to 不为空)
    2. @提及 (消息开头或包含 @人格名/当前发言者 @了人格)
    3. 关键词触发
    4. 常规发言
    """
    content = current_content.strip() if isinstance(current_content, str) else ""

    # 1. 回复关系优先: 若客户端提供了 reply_to 元数据
    if reply_to:
        return TriggerReason(
            reason="reply",
            description=f"当前发言者正在回复 {reply_to} 的消息。你应在回应中适当承接上下文。",
            target=reply_to,
        )

    # 2. @提及检测: 检查消息中是否有 @ 当前发言者的称呼
    # AstrBot 格式: <current_speaker identity="..."> 包裹当前消息
    if channel_type == "group":
        mentioned = _extract_mention_target(content)
        if mentioned:
            return TriggerReason(
                reason="mentioned",
                description="当前发言者 @提及了你。直接回应此人，不需要询问是否被呼叫。",
            )

    # 3. 常规发言
    return TriggerReason(
        reason="normal",
        description=(
            "这是一条常规发言。如果对方在和你对话就自然回应；"
            "如果看起来是群里其他人之间的对话，介入应克制。"
        ),
    )


def _extract_mention_target(content: str) -> str | None:
    """检测消息是否包含 @人格的模式.

    常见格式:
    - @昵称 开头
    - <current_speaker> 标签内含 @
    - 中文 @名字
    """
    # AstrBot current_speaker 标签包裹: <current_speaker identity="...">\n内容\n</current_speaker>
    inner = re.sub(r"</?current_speaker[^>]*>", "", content).strip()
    # @开头或包含 @xxx 模式
    if re.match(r"^@[\w一-鿿]+", inner):
        return inner.split()[0].lstrip("@")
    return None


def format_trigger_reason(reason: TriggerReason) -> str:
    """格式化为 Prompt 文本."""
    labels = {
        "mentioned": "被 @提及",
        "reply": "回复关系",
        "keyword": "关键词触发",
        "normal": "常规发言",
    }
    label = labels.get(reason.reason, reason.reason)
    return f"- 触发原因：{label}\n- 提示：{reason.description}"
