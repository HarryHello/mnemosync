"""工具: 句子分类器.

提示词清洗 Agent 通过 function_call 调用此工具,
判断一个句子属于"人格描述"还是"功能性指令"。

提示词模板由 PromptStore 加载 (registry 名: `sentence_classifier`).
默认: `src/core/agents/prompts/defaults/sentence_classifier.md`.
覆盖: `data/prompts/sentence_classifier.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from langchain_core.tools import tool

from src.core.prompts import get_prompt_store
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType


def build_classifier_prompt(text: str) -> str:
    """构建句子分类的 user prompt."""
    return get_prompt_store().load("sentence_classifier").replace("__TEXT__", text)


@dataclass
class SentenceClassifyResult:
    """句子分类结果."""

    type: str  # "persona" | "instruction" | "ambiguous"
    confidence: float  # 0.0-1.0
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def classify_sentence(
    forwarder: MultiForwarder, text: str,
) -> SentenceClassifyResult:
    """调用辅助模型对单句分类 (非 Tool 部分, 便于直接复用)."""
    messages = [
        {"role": "system", "content": "你是句子分类助手。只返回 JSON。"},
        {"role": "user", "content": build_classifier_prompt(text)},
    ]
    resp = await forwarder.chat(
        ModelType.ASSIST, messages=messages, temperature=0.1,
        response_format={"type": "json_object"},
        extra_body={"enable_thinking": False},
    )
    content = resp["choices"][0]["message"]["content"].strip()
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    data = json.loads(content)
    return SentenceClassifyResult(
        type=data.get("type", "ambiguous"),
        confidence=float(data.get("confidence", 0.5)),
        reasoning=data.get("reasoning", ""),
    )


def make_sentence_classifier_tool(forwarder: MultiForwarder):
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