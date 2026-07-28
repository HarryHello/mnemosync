"""表达习惯提取测试."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.core.memory.expression_style import extract_style_from_turns


@dataclass
class FakeTurn:
    role: str
    content: str
    event_type: str = "message"
    ts: datetime | None = None


def test_extract_empty_when_few_samples():
    """样本不足时返回空风格."""
    turns = [FakeTurn(role="assistant", content="好的")]
    style = extract_style_from_turns(turns, "space-1", min_samples=3)
    assert style.sample_count == 1
    assert style.to_memory_content() == ""


def test_extract_particle_preference():
    """提取句末语气词偏好."""
    turns = [
        FakeTurn(role="assistant", content="好的呢"),
        FakeTurn(role="assistant", content="可以呀"),
        FakeTurn(role="assistant", content="没问题呢"),
        FakeTurn(role="assistant", content="行吧"),
        FakeTurn(role="assistant", content="知道了呀"),
    ]
    style = extract_style_from_turns(turns, "space-1")
    content = style.to_memory_content()
    assert "语气词" in content
    assert style.sample_count == 5
    # 呢/呀/吧 应有较高频率
    assert style.particle_freq.get("呢", 0) > 0


def test_extract_punctuation_preference():
    """提取标点偏好."""
    turns = [
        FakeTurn(role="assistant", content="好的~"),
        FakeTurn(role="assistant", content="可以~"),
        FakeTurn(role="assistant", content="没问题！"),
        FakeTurn(role="assistant", content="行吧…"),
    ]
    style = extract_style_from_turns(turns, "space-1")
    assert style.punct_freq.get("tilde", 0) > 0
    assert style.punct_freq.get("exclamation", 0) > 0


def test_extract_short_sentence_preference():
    """提取短句偏好."""
    turns = [
        FakeTurn(role="assistant", content="好"),
        FakeTurn(role="assistant", content="嗯"),
        FakeTurn(role="assistant", content="行"),
    ]
    style = extract_style_from_turns(turns, "space-1")
    assert style.short_sentence_ratio > 0.5
    content = style.to_memory_content()
    assert "短句" in content


def test_extract_response_patterns():
    """提取常见回应模式."""
    turns = [
        FakeTurn(role="assistant", content="好的，没问题"),
        FakeTurn(role="assistant", content="好的，我知道了"),
        FakeTurn(role="assistant", content="嗯，让我看看"),
        FakeTurn(role="assistant", content="好的，这就来"),
    ]
    style = extract_style_from_turns(turns, "space-1")
    assert style.response_pattern_freq.get("好的", 0) > 0.3
    content = style.to_memory_content()
    assert "简短回应" in content


def test_ignore_tool_calls():
    """tool_call 事件不计入表达习惯."""
    turns = [
        FakeTurn(role="assistant", content='{"function": "poke"}', event_type="tool_call"),
        FakeTurn(role="assistant", content='{"function": "react"}', event_type="tool_call"),
        FakeTurn(role="assistant", content="好的呢"),
    ]
    style = extract_style_from_turns(turns, "space-1", min_samples=1)
    # 只有1条有效消息 (tool_call 被排除)
    assert style.sample_count == 1


def test_ignore_user_messages():
    """user 消息不计入表达习惯."""
    turns = [
        FakeTurn(role="user", content="你好"),
        FakeTurn(role="user", content="在吗"),
        FakeTurn(role="assistant", content="好的呢"),
        FakeTurn(role="assistant", content="可以呀"),
        FakeTurn(role="assistant", content="没问题呢"),
    ]
    style = extract_style_from_turns(turns, "space-1")
    assert style.sample_count == 3  # 只有 assistant 消息


def test_long_sentence_preference():
    """长句偏好检测."""
    long_text = "关于你提到的这个问题，我认为我们需要从多个角度来考虑，包括技术实现、用户体验以及长期维护成本等因素。"
    turns = [
        FakeTurn(role="assistant", content=long_text),
        FakeTurn(role="assistant", content=long_text),
        FakeTurn(role="assistant", content=long_text),
    ]
    style = extract_style_from_turns(turns, "space-1")
    assert style.avg_sentence_length > 25
    content = style.to_memory_content()
    assert "较长句式" in content
