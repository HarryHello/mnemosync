"""LangChain Tool 封装层.

3 个工具供 Agent 通过 function_call 调用:
- vector_search: 向量语义检索
- emotion_analyzer: 情绪分析
- update_addressing: 关系称呼动态更新 (v0.2.10)

v0.2.12: 移除 time_decay_calculator(衰减已改为确定性公式)和 sentence_classifier(提示词清洗已改为单次重写).
"""

from .emotion_analyzer import EmotionResult, analyze_emotion, make_emotion_analyzer_tool
from .update_addressing import make_update_addressing_tool
from .vector_search import (
    MemoryRetriever,
    RetrievedMemory,
    make_vector_search_tool,
)

__all__ = [
    "MemoryRetriever",
    "RetrievedMemory",
    "make_vector_search_tool",
    "EmotionResult",
    "analyze_emotion",
    "make_emotion_analyzer_tool",
    "make_update_addressing_tool",
]