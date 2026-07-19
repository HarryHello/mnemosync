"""LangChain Tool 封装层.

5 个工具供 Agent 通过 function_call 调用:
- vector_search: 向量语义检索
- emotion_analyzer: 情绪分析
- time_decay_calculator: 时间衰减计算
- classify_sentence_type: 句子分类 (提示词清洗)
- update_addressing: 关系称呼动态更新 (v0.2.10)
"""

from .emotion_analyzer import EmotionResult, analyze_emotion, make_emotion_analyzer_tool
from .sentence_classifier import (
    SentenceClassifyResult,
    classify_sentence,
    make_sentence_classifier_tool,
)
from .time_decay_calculator import DecayResult, calculate_decay, make_time_decay_calculator_tool
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
    "DecayResult",
    "calculate_decay",
    "make_time_decay_calculator_tool",
    "SentenceClassifyResult",
    "classify_sentence",
    "make_sentence_classifier_tool",
    "make_update_addressing_tool",
]
