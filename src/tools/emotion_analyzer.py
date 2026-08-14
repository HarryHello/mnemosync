"""工具: 情绪分析.

记忆分析 Agent 和关系分析 Agent 通过 function_call 调用.
调用辅助模型分析文本情绪, 输出标签+强度+类别.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.tools import tool

from src.infra.debug_context import use_agent
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

EMOTION_PROMPT = """你是情绪分析助手。分析以下文本的情绪内容，以 JSON 格式返回：

预期字段：
- emotion: happy|sad|angry|anxious|neutral|excited|grateful|stressed
- intensity: 0.0-1.0
- valence: -1.0~1.0 情绪效价 (负=消极, 正=积极, 0=中性; 只反映好坏程度, 不区分具体情绪)
- category: casual_chat|health_disclosure|personal_sharing|preference_statement|emotional_expression|complaint|gratitude|other
- keywords: 关键词列表
- summary: 一句话概括情绪内容

规则：
- emotion: 主要情绪, 无法判断用 neutral
- intensity: 情绪强度, 闲聊 0.1-0.3, 强烈情绪 0.7-1.0
- valence: 按 emotion 与强度推算效价 (如 angry/sad/stressed 为负, happy/excited/grateful 为正, 幅度随 intensity)
- category: 对话类型分类
- 不要过度解读: 只分析明确表达的情绪

待分析文本：
{text}

只返回 JSON, 不要其他内容。"""


#: emotion 标签 → valence 基准 (无显式 valence 时兜底, 幅度由 intensity 缩放)
_EMOTION_VALENCE_BASE: dict[str, float] = {
    "happy": 0.8, "excited": 0.9, "grateful": 0.7,
    "neutral": 0.0,
    "sad": -0.8, "angry": -0.9, "anxious": -0.6, "stressed": -0.7,
}


def _valence_fallback(emotion: str, intensity: float) -> float:
    """emotion + intensity → valence 兜底映射 (LLM 未输出 valence 时)."""
    base = _EMOTION_VALENCE_BASE.get(emotion, 0.0)
    return max(-1.0, min(1.0, base * min(1.0, max(0.2, intensity) * 1.2)))


@dataclass
class EmotionResult:
    emotion: str
    intensity: float
    category: str
    keywords: list[str]
    summary: str
    valence: float = 0.0  # v0.4.1: 情绪效价 (-1~1), mood 冲击输入

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def analyze_emotion(
    forwarder: MultiForwarder, text: str,
) -> EmotionResult:
    """调用辅助模型分析文本情绪（非 Tool 部分, 便于直接复用）."""
    messages = [
        {"role": "system", "content": "你是情绪分析助手。只返回 JSON。"},
        {"role": "user", "content": EMOTION_PROMPT.format(text=text)},
    ]
    with use_agent("emotion_analysis"):
        resp = await forwarder.chat(
            ModelType.ASSIST, messages=messages, temperature=0.1,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
    content = resp["choices"][0]["message"]["content"].strip()
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    data = json.loads(content)
    intensity = float(data.get("intensity", 0.3))
    emotion = data.get("emotion", "neutral")
    raw_valence = data.get("valence")
    try:
        valence = float(raw_valence) if raw_valence is not None else _valence_fallback(emotion, intensity)
    except (TypeError, ValueError):
        valence = _valence_fallback(emotion, intensity)
    return EmotionResult(
        emotion=emotion,
        intensity=intensity,
        category=data.get("category", "other"),
        keywords=data.get("keywords", []),
        summary=data.get("summary", ""),
        valence=max(-1.0, min(1.0, valence)),
    )


def make_emotion_analyzer_tool(forwarder: MultiForwarder) -> Any:
    """创建 emotion_analyzer LangChain Tool."""

    @tool
    async def emotion_analyzer(text: str) -> dict[str, Any]:
        """分析文本的情绪内容, 返回情绪标签、强度和类别.

        用于:
        - 记忆分析 Agent: ReAct 循环中分析对话内容的情感标签
        - 关系分析 Agent: 分析对话中的情感信号, 辅助亲密度计算

        Args:
            text: 需要分析的用户消息或对话片段

        Returns:
            {emotion, intensity, category, keywords, summary}
            - emotion: happy/sad/angry/anxious/neutral/excited/grateful/stressed
            - intensity: 0.0-1.0 情绪强度
            - category: 对话类型分类
        """
        result = await analyze_emotion(forwarder, text)
        return result.to_dict()

    return emotion_analyzer

