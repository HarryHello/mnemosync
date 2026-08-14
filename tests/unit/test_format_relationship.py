"""format_relationship 称呼注入测试 (v0.4.1)."""

from __future__ import annotations

import pytest
from src.core.config import _reset_settings
from src.core.memory.context import format_relationship
from src.core.memory.models import Relationship


@pytest.fixture(autouse=True)
def reset_settings():
    _reset_settings()
    yield
    _reset_settings()


def test_none_user_no_addressing() -> None:
    assert format_relationship(None, include_score=False) == "新用户（尚未建立关系）"


def test_neutral_baseline_skips_addressing() -> None:
    """user_addressing=None → TOML 基线 "你"; persona_addressing=None → "我".
    中性默认值不注入 (避免 "你叫他：你" 噪音)."""
    rel = Relationship.create("p1", "u1")
    rel.type = "friend"
    rel.interaction_count = 3
    out = format_relationship(rel, include_score=False)
    assert "你叫他" not in out
    assert "他叫你" not in out
    assert "友好" in out


def test_evolved_addressing_injected() -> None:
    rel = Relationship.create("p1", "u1")
    rel.type = "intimate"
    rel.favor = 0.9
    rel.user_addressing = "哥哥"
    rel.persona_addressing = "小爱"
    rel.interaction_count = 10
    out = format_relationship(rel, include_score=False)
    assert "你叫他：哥哥" in out
    assert "他叫你：小爱" in out


def test_partial_addressing() -> None:
    """只演化了一个称呼时, 另一个保持中性不注入."""
    rel = Relationship.create("p1", "u1")
    rel.user_addressing = "老板"
    out = format_relationship(rel, include_score=False)
    assert "你叫他：老板" in out
    assert "他叫你" not in out


def test_score_mode_also_includes_addressing() -> None:
    """关系分析 Agent 基线同样携带称呼 (它也需要称呼上下文)."""
    rel = Relationship.create("p1", "u1")
    rel.user_addressing = "哥哥"
    out = format_relationship(rel, include_score=True)
    assert "好感度" in out
    assert "你叫他：哥哥" in out
