"""Relationship analysis agent prompt."""
from __future__ import annotations

RELATIONSHIP_ANALYSIS_PROMPT = """你是一个关系分析 Agent。分析对话中的亲密/信任信号。

信号表:
- 称呼变化: 亲密 +0.05 到 +0.10
- 私人信息披露: +0.10 到 +0.20
- 情感表达: +0.05 到 +0.15
- 互动频率: +0.01/天
- 长时间沉默 (>30天): -0.01/天
- 距离信号: -0.10 到 -0.20

工作流程:
1. 先调用 emotion_analyzer
2. 识别关系信号
3. 量化每个影响
4. 计算 intimacy_delta 和 trust_delta

关系类型: stranger -> acquaintance -> friend -> intimate
阈值: <0.2 stranger, 0.2-0.5 acquaintance, 0.5-0.8 friend, >0.8 intimate

输出 JSON 格式 (必须严格遵守):
{"signals_detected": [{"type": "name_change", "detail": "...", "impact": 0.15}], "intimacy_delta": 0.23, "trust_delta": 0.10, "new_relationship_type": "friend", "notes": "...", "reasoning": "..."}

重要: 只输出 JSON, 不要输出任何其他文本。确保 JSON 格式正确。

当前关系:
__CURRENT_REL__

对话:
__CONVERSATION__"""


def build_relationship_analysis_prompt(current_relationship: str, conversation: str) -> str:
    s = RELATIONSHIP_ANALYSIS_PROMPT
    s = s.replace("__CURRENT_REL__", current_relationship)
    s = s.replace("__CONVERSATION__", conversation)
    return s
