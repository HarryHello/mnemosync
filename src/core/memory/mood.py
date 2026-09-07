"""全局人格情绪状态 (mood) — v0.4.1, RFC §4.

- 连续 valence ∈ [-1, 1] (好-坏), **全局 per-persona**, 跨空间共享
- 标签只用"心情好坏"级 (6 段), 不细分具体情绪 (单维 valence 无法区分愤怒/伤心)
- 具体情绪 (愤怒/伤心等) 写入 cause (自然语言, 可含明确情绪词), 由写路径保证脱敏
- 更新: 惯性 0.8 + 时间衰减 + 单步 clamp ≤ 0.3 + 最小阈值 0.05 (情绪变化克制)
- 幂等: 同一 interaction_id 只冲击一次 (工具续轮/重放不重复扰动)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: mood 阶段投影 (使用时): 心情级描述, 边界左闭右开.
#: 标签为全英文内部标识 (v0.4.1); 注入模型/面板显示用 MOOD_LABELS_ZH 中文映射.
MOOD_TIERS: tuple[tuple[str, float, float], ...] = (
    ("dreadful", -1.0, -0.6),
    ("bad", -0.6, -0.2),
    ("low", -0.2, 0.0),
    ("decent", 0.0, 0.2),
    ("good", 0.2, 0.6),
    ("great", 0.6, 1.0),
)

#: 心情级标签 → 中文显示 (注入主对话 system / 面板表头)
MOOD_LABELS_ZH: dict[str, str] = {
    "dreadful": "心情极差",
    "bad": "心情差",
    "low": "心情不佳",
    "decent": "心情不错",
    "good": "心情好",
    "great": "心情极好",
}


def mood_label_zh(tier: str) -> str:
    """英文 tier → 中文心情描述 (未知回退原文)."""
    return MOOD_LABELS_ZH.get(tier, tier)

#: 更新参数 (RFC §4.2)
MOOD_INERTIA = 0.8          # 惯性: 情绪不随单条消息剧烈跳变
MOOD_STEP_CLAMP = 0.3       # 单步变化上限: 不跨极性跳变
MOOD_MIN_DELTA = 0.05       # 最小变化阈值: 普通闲聊不扰动
MOOD_DECAY_PER_HOUR = 0.02  # 时间衰减: 每消失 0.02 强度/小时 (~50h 回中性)

#: mood 冲击强度系数 (情绪分析 valence 的缩放)
MOOD_IMPACT_ALPHA = 0.5


def mood_tier_from_valence(valence: float) -> str:
    """valence → 心情级标签."""
    v = max(-1.0, min(1.0, valence))
    for label, lo, hi in MOOD_TIERS:
        if lo <= v < hi:
            return label
    return "great"  # v == 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def apply_mood_impact(
    old_valence: float,
    impact: float,
    *,
    now: datetime,
    updated_at: datetime | None,
    inertia: float = MOOD_INERTIA,
    step_clamp: float = MOOD_STEP_CLAMP,
    min_delta: float = MOOD_MIN_DELTA,
    decay_per_hour: float = MOOD_DECAY_PER_HOUR,
) -> tuple[float, bool]:
    """计算一次 mood 更新. 返回 (新 valence, 是否发生变化).

    顺序: 时间衰减 → 惯性混合冲击 → 单步 clamp → 最小阈值判定.
    """
    # 1. 时间衰减: 情绪随时间自然消退回中性
    decayed = old_valence
    if updated_at is not None and decayed != 0.0:
        hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
        decay_amount = min(abs(decayed), decay_per_hour * hours)
        decayed = decayed - (1.0 if decayed > 0 else -1.0) * decay_amount

    # 2. 惯性混合冲击: 新值向冲击方向移动 20%
    target = inertia * decayed + (1.0 - inertia) * impact

    # 3. 单步上限
    delta = _clamp(target - decayed, -step_clamp, step_clamp)

    # 4. 最小阈值: 变化过小不更新 (防抖动)
    if abs(delta) < min_delta:
        return decayed, False

    return _clamp(decayed + delta, -1.0, 1.0), True


def _mood_impact_from_emotion(emotion_analysis: dict[str, Any]) -> float:
    """情绪分析结果 → mood 冲击 (valence × 系数)."""
    valence = float(emotion_analysis.get("valence", 0.0) or 0.0)
    return _clamp(valence * MOOD_IMPACT_ALPHA, -1.0, 1.0)


async def run_emotion_mood_channel(
    forwarder: Any,
    persona_store: Any,
    persona_id: str,
    *,
    interaction_id: str | None,
    extracted: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """前置情绪通道 (非流式/流式共用): 情绪分析 + mood 更新, 失败降级.

    返回 (emotion_analysis, mood_state); 任一步失败都不阻塞主流程
    (情绪分析失败 → 空 dict, mood 保持原值).
    """
    emotion_analysis: dict[str, Any] = {}
    mood_state: dict[str, Any] | None = None
    try:
        from src.core.graph.nodes import _compute_emotion

        emotion_analysis = await _compute_emotion(forwarder, extracted)
        if persona_store is not None:
            mood_state = await update_persona_mood(
                persona_store,
                persona_id,
                interaction_id=interaction_id,
                emotion_analysis=emotion_analysis,
                cause=emotion_analysis.get("summary") or None,
            )
    except Exception as e:
        logger.debug("情绪/mood 前置通道失败 (降级): %s", e)
    return emotion_analysis, mood_state


async def load_subject_anchor(memory_store: Any, actor_id: str | None) -> str:
    """加载当前说话者的对象化情绪锚点 (EPHEMERAL, RFC §6.2).

    返回注入文本 (空串 = 无锚点). 失败静默降级.
    """
    if not actor_id or memory_store is None:
        return ""
    try:
        anchors = await memory_store.list_ephemeral_by_subject(actor_id, limit=1)
        if anchors:
            content = anchors[0].content
            return "对当前发言者的近期情绪：" + str(content)
    except Exception as e:
        logger.debug("锚点加载失败 (忽略): %s", e)
    return ""


async def update_persona_mood(
    persona_store: Any,
    persona_id: str,
    *,
    interaction_id: str | None,
    emotion_analysis: dict[str, Any],
    cause: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """前置情绪通道: 更新人格 mood 并返回最新状态 (RFC §4.2).

    - 幂等: 同一 interaction_id 只冲击一次 (跳过重复冲击, 仍返回当前状态)
    - 失败降级: store 异常时返回当前值, 不阻塞主流程

    Returns:
        {valence, tier, cause, updated_at, changed}
    """
    now = now or datetime.now(UTC)
    impact = _mood_impact_from_emotion(emotion_analysis)
    try:
        cur = await persona_store.get_mood(persona_id)
    except Exception as e:
        logger.warning("读取 mood 失败 (降级中性): %s", e)
        cur = {"valence": 0.0, "cause": None, "updated_at": None}

    old_valence = float(cur.get("valence") or 0.0)
    old_updated = cur.get("updated_at")
    old_interaction = cur.get("last_interaction_id")
    try:
        old_updated_dt = datetime.fromisoformat(old_updated) if old_updated else None
    except ValueError:
        old_updated_dt = None

    # 幂等守卫: 同一交互已冲击过 → 不重复
    if interaction_id and old_interaction == interaction_id:
        return {
            "valence": old_valence,
            "tier": mood_tier_from_valence(old_valence),
            "cause": cur.get("cause") if cur.get("cause") else cause,
            "updated_at": old_updated,
            "changed": False,
        }

    new_valence, changed = apply_mood_impact(
        old_valence, impact, now=now, updated_at=old_updated_dt,
    )
    eff_cause = cause if cause is not None else cur.get("cause")
    if changed:
        try:
            await persona_store.set_mood(
                persona_id,
                valence=new_valence,
                cause=eff_cause,
                interaction_id=interaction_id,
            )
        except Exception as e:
            logger.warning("写入 mood 失败 (不影响主流程): %s", e)
            changed = False
    return {
        "valence": new_valence,
        "tier": mood_tier_from_valence(new_valence),
        "cause": eff_cause,
        "updated_at": old_updated if not changed else None,
        "changed": changed,
    }
