"""工具: 句子分类器.

提示词清洗 Agent 通过 function_call 调用此工具,
判断一个句子属于"人格描述"还是"功能性指令"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from langchain_core.tools import tool

from src.core.config import get_settings
from src.infra import Forwarder

CLASSIFIER_PROMPT = """你是句子分类助手。判断以下句子属于"人格描述"还是"功能性指令"。

分类标准:
- **persona (人格描述)**: 设定 AI 的身份、名字、性格、语气、背景故事、角色定位等
  例: "你是一个傲娇的妹妹"、"你的名字叫小夜"、"你要用可爱的语气说话"
- **instruction (功能性指令)**: 约束 AI 的行为、输出格式、工具使用、规则等
  例: "请用 JSON 格式回复"、"回复不得超过 100 字"、"不要使用表情符号"

规则:
- 如果句子明确描述了"AI 是谁/是什么性格/叫什么名字", 归类为 persona
- 如果句子描述了"AI 应该怎么做事/怎么输出/遵守什么规则", 归类为 instruction
- 如果两者都有 (如 "你是一个助手, 用 JSON 回复"), 按主要意图判断
- 如果完全无法判断, 归类为 ambiguous

待分类句子:
{text}

以 JSON 格式返回, 不要其他内容:
{{"type": "persona", "confidence": 0.95, "reasoning": "该句描述了 AI 的性格特征"}}"""


@dataclass
class SentenceClassifyResult:
    """句子分类结果."""

    type: str  # "persona" | "instruction" | "ambiguous"
    confidence: float  # 0.0-1.0
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def classify_sentence(
    forwarder: Forwarder, text: str, model: str | None = None
) -> SentenceClassifyResult:
    """调用辅助模型对单句分类 (非 Tool 部分, 便于直接复用)."""
    settings = get_settings()
    model = model or settings.chat.assist_model

    messages = [
        {"role": "system", "content": "你是句子分类助手。只返回 JSON。"},
        {"role": "user", "content": CLASSIFIER_PROMPT.format(text=text)},
    ]
    resp = await forwarder.chat(
        messages=messages, model=model, temperature=0.1,
        response_format={"type": "json_object"},
        extra_body={"enable_thinking": False},
    )
    content = resp["choices"][0]["message"]["content"].strip()
    # 防御性剥离 <think>...</think> (即使关闭 thinking 也可能有残留)
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    data = json.loads(content)
    return SentenceClassifyResult(
        type=data.get("type", "ambiguous"),
        confidence=float(data.get("confidence", 0.5)),
        reasoning=data.get("reasoning", ""),
    )


def make_sentence_classifier_tool(forwarder: Forwarder):
    """创建 classify_sentence_type LangChain Tool."""

    @tool
    async def classify_sentence_type(text: str) -> dict[str, Any]:
        """判断一个句子属于"人格描述"还是"功能性指令".

        用于提示词清洗 Agent: ReAct 循环中对客户端 system 消息的每个句子分类,
        人格描述将被丢弃, 功能性指令将被保留。

        Args:
            text: 待分类的单个句子

        Returns:
            {type, confidence, reasoning}
            - type: "persona" | "instruction" | "ambiguous"
            - confidence: 0.0-1.0 分类置信度
            - reasoning: 分类理由
        """
        result = await classify_sentence(forwarder, text)
        return result.to_dict()

    return classify_sentence_type