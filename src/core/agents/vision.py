"""Vision Description Agent: 将图片转述为文字描述.

独立 VISION 角色 (可在面板单独绑定图片模型); 未绑定候选时回退 ASSIST,
升级后零配置可用. 提示词走 PromptStore (面板可编辑), 默认层
`src/core/agents/prompts/defaults/vision_description.md`.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.models.resolver import NoCandidateForRoleError
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

logger = logging.getLogger(__name__)

# 兜底常量: PromptStore 不可用时降级使用 (与 defaults/vision_description.md 一致).
VISION_SYSTEM_PROMPT = """你是一个图片描述助手。你的任务是将图片转换为详细的文字描述，以便不支持视觉的模型能够理解图片内容。

规则：
1. 描述应该详细但简洁，包含图片中的关键信息
2. 如果图片包含文字，完整转录文字内容
3. 如果图片是截图、图表或界面，描述其结构和关键元素
4. 使用中文描述
5. 只输出描述，不要添加额外解释"""


def _load_vision_prompt() -> str:
    """加载视觉转写提示词 (PromptStore 可编辑; 不可用时用内置常量兜底)."""
    try:
        from src.core.prompts import get_prompt_store

        return get_prompt_store().load("vision_description")
    except Exception:
        return VISION_SYSTEM_PROMPT


async def describe_image(
    forwarder: MultiForwarder,
    image_content: dict[str, Any],
) -> str:
    """调用视觉模型描述图片.

    Args:
        forwarder: 多候选转发器 (VISION 角色, 未绑定候选时回退 ASSIST)
        image_content: 图片 content part, 格式如:
            {"type": "image_url", "image_url": {"url": "https://..."}}
            或 {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    Returns:
        图片的文字描述
    """
    system_prompt = _load_vision_prompt()
    # 构建带 content parts 的消息
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
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
            ModelType.VISION,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        description = resp["choices"][0]["message"]["content"] or ""
        logger.debug("  ✅ 图片描述完成, 长度: %d", len(description))
        return description
    except NoCandidateForRoleError:
        logger.debug("  [vision] 未绑定 vision 角色, 回退 assist")
        resp = await forwarder.chat(
            ModelType.ASSIST,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        description = resp["choices"][0]["message"]["content"] or ""
        logger.debug("  ✅ 图片描述完成 (assist 回退), 长度: %d", len(description))
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
