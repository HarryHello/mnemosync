"""表达习惯提取与应用.

从最近的 assistant 回复中确定性地提取常见表达模式，
供 Expressor 在改写时参考，使回复风格与当前空间一致。

提取维度:
- 语气词频率: 句末"呢""吧""呀""哈""嘛"等的使用偏好
- 标点偏好: "~""！""…""。"等
- 句长偏好: 短句（平均句长 < 10）/ 中句 / 长句
- 常见回应: "好的""嗯""行""可以"等高频回应模式

不使用 LLM，纯确定性规则，成本为零。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 句末语气词
_FINAL_PARTICLES = ["呢", "吧", "呀", "哈", "嘛", "哦", "哇", "啦", "呗", "哟"]
# 标点偏好
_PUNCT_PATTERNS = {
    "tilde": re.compile(r"~"),
    "exclamation": re.compile(r"[!！]"),
    "ellipsis": re.compile(r"[.。…]{2,}"),
    "period": re.compile(r"[。.]"),
    "question": re.compile(r"[?？]"),
}
# 常见回应模式
_RESPONSE_PATTERNS = [
    "好的", "嗯", "行", "可以", "收到", "ok", "OK", "好",
    "了解", "明白", "知道了", "没问题",
]


@dataclass
class ExpressionStyle:
    """一个空间的表达习惯总结."""

    space_id: str
    sample_count: int = 0

    # 语气词偏好: particle -> 频率 (0-1)
    particle_freq: dict[str, float] = field(default_factory=dict)
    # 标点偏好: punct_type -> 频率 (0-1)
    punct_freq: dict[str, float] = field(default_factory=dict)
    # 平均句长 (字符数)
    avg_sentence_length: float = 0.0
    # 短句比例 (< 10 字符的句子占比)
    short_sentence_ratio: float = 0.0
    # 常见回应模式频率
    response_pattern_freq: dict[str, float] = field(default_factory=dict)

    def to_memory_content(self) -> str:
        """序列化为记忆内容文本 (供 Expressor prompt 使用)."""
        parts: list[str] = []
        if self.sample_count < 3:
            return ""

        # 语气词
        top_particles = sorted(
            self.particle_freq.items(), key=lambda x: x[1], reverse=True,
        )[:3]
        particle_hints = [p for p, f in top_particles if f > 0.15]
        if particle_hints:
            parts.append(f"常用句末语气词: {'、'.join(particle_hints)}")

        # 标点
        punct_hints: list[str] = []
        for name, freq in self.punct_freq.items():
            if freq > 0.2:
                label = {
                    "tilde": "波浪号~",
                    "exclamation": "感叹号",
                    "ellipsis": "省略号",
                    "question": "问号",
                }.get(name, name)
                punct_hints.append(label)
        if punct_hints:
            parts.append(f"常用标点: {'、'.join(punct_hints)}")

        # 句长
        if self.short_sentence_ratio > 0.5:
            parts.append("偏好短句回复")
        elif self.avg_sentence_length > 25:
            parts.append("偏好较长句式")

        # 回应模式
        top_responses = sorted(
            self.response_pattern_freq.items(), key=lambda x: x[1], reverse=True,
        )[:2]
        response_hints = [r for r, f in top_responses if f > 0.1]
        if response_hints:
            parts.append(f"常用简短回应: {'、'.join(response_hints)}")

        return "；".join(parts) if parts else ""


def _split_sentences(text: str) -> list[str]:
    """将文本拆分为句子."""
    # 按句号/问号/感叹号/换行拆分
    sentences = re.split(r"[。.！!？?\n]+", text)
    return [s.strip() for s in sentences if s.strip()]


def extract_style_from_turns(
    turns: list[Any],
    space_id: str,
    *,
    min_samples: int = 3,
) -> ExpressionStyle:
    """从 assistant 回复列表中提取表达习惯.

    Args:
        turns: ConversationTurn 列表 (仅取 role=assistant)
        space_id: 空间 ID
        min_samples: 最少样本数，低于此数返回空风格

    Returns:
        ExpressionStyle; 样本不足时 sample_count < min_samples
    """
    assistant_texts = [
        t.content for t in turns
        if getattr(t, "role", "") == "assistant"
        and getattr(t, "content", "")
        and getattr(t, "event_type", "message") == "message"
    ]

    style = ExpressionStyle(space_id=space_id, sample_count=len(assistant_texts))
    if len(assistant_texts) < min_samples:
        return style

    # 合并所有文本
    all_text = "\n".join(assistant_texts[-20:])  # 取最近 20 条
    sentences = _split_sentences(all_text)
    if not sentences:
        return style

    # 句长统计
    lengths = [len(s) for s in sentences]
    style.avg_sentence_length = sum(lengths) / len(lengths)
    style.short_sentence_ratio = sum(1 for n in lengths if n < 10) / len(lengths)

    # 语气词频率: 句末出现语气词的句子占比
    particle_counts: dict[str, int] = dict.fromkeys(_FINAL_PARTICLES, 0)
    for s in sentences:
        for p in _FINAL_PARTICLES:
            if s.endswith(p):
                particle_counts[p] += 1
                break
    total = len(sentences)
    style.particle_freq = {
        p: c / total for p, c in particle_counts.items() if c > 0
    }

    # 标点频率
    for name, pattern in _PUNCT_PATTERNS.items():
        count = len(pattern.findall(all_text))
        if count > 0:
            style.punct_freq[name] = min(count / total, 1.0)

    # 回应模式频率
    for resp in _RESPONSE_PATTERNS:
        count = sum(1 for t in assistant_texts if t.strip().lower().startswith(resp.lower()))
        if count > 0:
            style.response_pattern_freq[resp] = count / len(assistant_texts)

    return style
