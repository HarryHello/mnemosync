"""测试 src/core/memory/models.py 的纯逻辑.

覆盖 DecayState.from_priority 分档, MemoryEntry.compute_theoretical_priority
的时间衰减公式, 关系 apply_delta 的 clamp 行为.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.core.memory.models import (
    DECAY_RATE_TO_HALF_LIFE,
    DecayState,
    MemoryEntry,
    MemoryType,
    Relationship,
    decay_rate_to_half_life,
)

# ─── DecayState.from_priority 分档 ───────────────────────


@pytest.mark.parametrize(
    "priority, expected",
    [
        (1.0, DecayState.ACTIVE),
        (0.31, DecayState.ACTIVE),
        (0.30, DecayState.DORMANT),  # 边界: 恰好 0.3 归 DORMANT
        (0.15, DecayState.DORMANT),
        (0.10, DecayState.WEAK),  # 边界
        (0.07, DecayState.WEAK),
        (0.05, DecayState.FORGOTTEN),
        (0.0, DecayState.FORGOTTEN),
    ],
)
def test_from_priority_thresholds(priority, expected):
    assert DecayState.from_priority(priority) == expected


# ─── decay_rate → 半衰期 ─────────────────────────────────


def test_decay_rate_zero_means_never():
    assert decay_rate_to_half_life(0.0) is None


@pytest.mark.parametrize("rate", list(DECAY_RATE_TO_HALF_LIFE.keys()))
def test_decay_rate_exact_hits(rate):
    assert decay_rate_to_half_life(rate) == DECAY_RATE_TO_HALF_LIFE[rate]


def test_decay_rate_nearest_neighbor():
    """未在表内的 rate 映射到最近邻."""
    assert decay_rate_to_half_life(0.11) == DECAY_RATE_TO_HALF_LIFE[0.1]
    assert decay_rate_to_half_life(0.29) == DECAY_RATE_TO_HALF_LIFE[0.3]


# ─── MemoryEntry.compute_theoretical_priority ─────────────


def test_permanent_priority_is_one():
    entry = MemoryEntry.create(
        content="p", role="user", memory_type=MemoryType.PERMANENT, importance=0.5,
    )
    assert entry.compute_theoretical_priority() == 1.0


def test_fresh_normal_priority_close_to_importance():
    entry = MemoryEntry.create(content="n", role="user", importance=0.6, decay_rate=0.3)
    p = entry.compute_theoretical_priority()
    assert 0.5 < p <= 0.6  # 尚未衰减, 无 access_bonus 也接近 importance


def test_old_normal_decays_below_importance():
    entry = MemoryEntry.create(content="n", role="user", importance=0.6, decay_rate=0.7)
    # 半衰期 17 天, 让它过去 34 天 → 剩 1/4
    entry.created_at = datetime.now(UTC) - timedelta(days=34)
    p = entry.compute_theoretical_priority()
    assert p < 0.6 * 0.5  # 至少一个半衰期以下
    assert p >= 0.0


def test_expired_normal_gets_penalty():
    entry = MemoryEntry.create(content="n", role="user", importance=1.0, decay_rate=0.1)
    entry.expires_at = datetime.now(UTC) - timedelta(days=1)
    p = entry.compute_theoretical_priority()
    assert p < 0.05  # 过期惩罚 0.01 × importance


def test_priority_never_exceeds_one():
    entry = MemoryEntry.create(content="n", role="user", importance=1.0, decay_rate=0.1)
    entry.access_count = 10_000  # 巨大 access_bonus
    assert entry.compute_theoretical_priority() <= 1.0


# ─── MemoryEntry.override_priority + is_forgotten ─────────


def test_override_priority_updates_forgotten_flag():
    entry = MemoryEntry.create(content="n", role="user", importance=0.8)
    entry.override_priority(0.02)
    assert entry.priority == pytest.approx(0.02)
    assert entry.is_forgotten is True

    entry.override_priority(0.5)
    assert entry.is_forgotten is False


def test_override_priority_clamps():
    entry = MemoryEntry.create(content="n", role="user")
    entry.override_priority(2.0)
    assert entry.priority == 1.0
    entry.override_priority(-0.5)
    assert entry.priority == 0.0


# ─── Relationship.apply_delta clamp ──────────────────────


def test_relationship_delta_clamps_upper():
    rel = Relationship.create("default", "u")
    rel.apply_delta(intimacy_delta=2.0, trust_delta=2.0)
    assert rel.intimacy_score == 1.0
    assert rel.trust_level == 1.0


def test_relationship_delta_clamps_lower():
    rel = Relationship.create("default", "u")
    rel.intimacy_score = 0.1
    rel.apply_delta(intimacy_delta=-5.0, trust_delta=-5.0)
    assert rel.intimacy_score == 0.0
    assert rel.trust_level == 0.0


def test_relationship_delta_increments_interaction():
    rel = Relationship.create("default", "u")
    rel.apply_delta(intimacy_delta=0.05, trust_delta=0.05)
    rel.apply_delta(intimacy_delta=0.05, trust_delta=0.05)
    assert rel.interaction_count == 2
