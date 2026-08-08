"""Vision Description Agent: 将图片转述为文字描述.

用于非多模态模型: 当用户发送图片但目标模型不支持视觉时,
先用 ASSIST 模型 (需支持视觉) 生成描述, 再替换为纯文本.
"""

from __future__ import annotations

import logging
from typing import Any

from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = """你是一个图片描述助手。你的任务是将图片转换为详细的文字描述，以便不支持视觉的模型能够理解图片内容。

规则：
1. 描述应该详细但简洁，包含图片中的关键信息
2. 如果图片包含文字，完整转录文字内容
3. 如果图片是截图、图表或界面，描述其结构和关键元素
4. 使用中文描述
5. 只输出描述，不要添加额外解释"""


async def describe_image(
    forwarder: MultiForwarder,
    image_content: dict[str, Any],
) -> str:
    """调用视觉模型描述图片.

    Args:
        forwarder: 多候选转发器 (ASSIST role)
        image_content: 图片 content part, 格式如:
            {"type": "image_url", "image_url": {"url": "https://..."}}
            或 {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    Returns:
        图片的文字描述
    """
    # 构建带 content parts 的消息
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请描述这张图片的内容。"},
                image_content,
            ],
        },
    ]

    logger.debug("🖼️ [vision] 开始描述图片...")
    try:
        resp = await forwarder.chat(
            ModelType.ASSIST,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        description = resp["choices"][0]["message"]["content"] or ""
        logger.debug("  ✅ 图片描述完成, 长度: %d", len(description))
        return description
    except Exception as e:
        logger.warning("图片描述失败: %s", e)
        return "[图片描述失败]"


def extract_image_parts(content: Any) -> list[dict[str, Any]]:
    """从 content 中提取图片 parts.

    Args:
        content: 消息的 content 字段 (str, list, 或 None)

    Returns:
        图片 content parts 列表, 如 [{"type": "image_url", "image_url": {...}}]
    """
    if not isinstance(content, list):
        return []
    return [
        part for part in content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


def has_image_parts(content: Any) -> bool:
    """检查 content 是否包含图片 parts."""
    return bool(extract_image_parts(content))


def strip_image_parts(content: Any) -> str:
    """从 content 中提取纯文本部分."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = [
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return "\n".join(texts) if texts else ""
