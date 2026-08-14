"""mood 状态机单元测试 (v0.4.1, RFC §4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.core.memory.mood import (
    MOOD_MIN_DELTA,
    MOOD_STEP_CLAMP,
    apply_mood_impact,
    mood_tier_from_valence,
    update_persona_mood,
)


def test_mood_tier_boundaries() -> None:
    assert mood_tier_from_valence(-0.9) == "心情极差"
    assert mood_tier_from_valence(-0.4) == "心情差"
    assert mood_tier_from_valence(-0.1) == "心情不佳"
    assert mood_tier_from_valence(0.1) == "心情不错"
    assert mood_tier_from_valence(0.4) == "心情好"
    assert mood_tier_from_valence(0.9) == "心情极好"
    assert mood_tier_from_valence(1.0) == "心情极好"  # 边界


def test_apply_mood_impact_inertia_limits_step() -> None:
    """单步变化受 step_clamp 限制 — 情绪不会瞬间跨极性."""
    now = datetime.now(UTC)
    # 中性 → 极强负冲击: 目标 = 0.2×(-1) = -0.2, 未超 clamp
    v1, c1 = apply_mood_impact(0.0, -1.0, now=now, updated_at=None)
    assert c1 and v1 == pytest.approx(-0.2)

    # 从 +0.8 → 极强负冲击: 目标 = 0.8×0.8 + 0.2×(-1) = 0.44, delta = -0.36 > clamp 0.3
    v2, c2 = apply_mood_impact(0.8, -1.0, now=now, updated_at=None)
    assert c2 and v2 == pytest.approx(0.5)  # 0.8 - 0.3


def test_apply_mood_impact_min_delta_skips() -> None:
    """冲击过小不更新 (防抖动)."""
    now = datetime.now(UTC)
    v, changed = apply_mood_impact(0.0, 0.1, now=now, updated_at=None)
    assert not changed
    assert v == 0.0


def test_apply_mood_impact_time_decay() -> None:
    """时间衰减: 50 小时后强情绪回中性."""
    now = datetime.now(UTC)
    updated = now - timedelta(hours=50)
    v, changed = apply_mood_impact(0.6, 0.0, now=now, updated_at=updated)
    assert v == pytest.approx(0.0, abs=0.01)
    assert not changed  # 衰减后无冲击 → 无变化


class FakePersonaStore:
    """内存版 persona store 桩."""

    def __init__(self) -> None:
        self.mood: dict[str, object] = {
            "valence": 0.0, "cause": None, "updated_at": None,
            "last_interaction_id": None,
        }

    async def get_mood(self, persona_id: str) -> dict[str, object]:
        return dict(self.mood)

    async def set_mood(self, persona_id: str, *, valence: float, cause: str | None,
                       interaction_id: str | None) -> bool:
        self.mood["valence"] = valence
        self.mood["cause"] = cause
        self.mood["updated_at"] = datetime.now(UTC).isoformat()
        self.mood["last_interaction_id"] = interaction_id
        return True


async def test_update_persona_mood_idempotent_per_interaction() -> None:
    """同一 interaction_id 只冲击一次."""
    store = FakePersonaStore()
    emotion = {"emotion": "angry", "intensity": 0.9, "valence": -0.9, "summary": "被羞辱"}

    r1 = await update_persona_mood(
        store, "p1", interaction_id="i-1", emotion_analysis=emotion, cause="被羞辱",
    )
    assert r1["changed"] is True
    assert r1["valence"] < 0

    # 同 interaction 重放 → 不重复冲击
    r2 = await update_persona_mood(
        store, "p1", interaction_id="i-1", emotion_analysis=emotion, cause="被羞辱",
    )
    assert r2["changed"] is False
    assert r2["valence"] == r1["valence"]

    # 新 interaction → 继续冲击
    r3 = await update_persona_mood(
        store, "p1", interaction_id="i-2", emotion_analysis=emotion, cause="被羞辱",
    )
    assert r3["changed"] is True


async def test_update_persona_mood_returns_tier_and_cause() -> None:
    store = FakePersonaStore()
    r = await update_persona_mood(
        store, "p1", interaction_id=None,
        emotion_analysis={"emotion": "happy", "intensity": 0.8, "valence": 0.8},
        cause="收到礼物",
    )
    # 单轮冲击温和: 0.8×0.5(系数) 惯性混合后仅小幅为正 (情绪变化克制)
    assert r["valence"] > 0
    assert r["tier"] == "心情不错"
    assert r["cause"] == "收到礼物"
    assert MOOD_MIN_DELTA > 0 and MOOD_STEP_CLAMP > 0  # 常量存在
