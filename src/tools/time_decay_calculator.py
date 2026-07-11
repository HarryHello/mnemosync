"""工具: 时间衰减计算.

记忆分析 Agent 在衰减评估步骤调用, 获取公式基线.
纯本地计算, 不调用外部 API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from math import log
from typing import Any

from langchain_core.tools import tool

from src.core.memory import (
    ACCESS_BONUS_FACTOR,
    DecayState,
    decay_rate_to_half_life,
)
from src.persistence.memory_store import SqliteMemoryStore


@dataclass
class DecayResult:
    memory_id: str
    days_elapsed: int
    half_life_days: int | None
    time_factor: float
    expiration_penalty: float
    access_bonus: float
    theoretical_priority: float
    days_to_forgotten: int | None
    current_state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def calculate_decay(
    memory_store: SqliteMemoryStore, memory_id: str
) -> DecayResult | None:
    """计算指定记忆的时间衰减状态.

    Returns None 若记忆不存在.
    """
    entry = await memory_store.get_by_id(memory_id)
    if entry is None:
        return None

    now = datetime.now(timezone.utc)
    days_elapsed = max(0, (now - entry.created_at).days)
    half_life = decay_rate_to_half_life(entry.decay_rate)

    # 永久记忆或 decay_rate=0: 不衰减
    if entry.is_permanent or half_life is None or half_life == 0:
        time_factor = 1.0
    else:
        time_factor = 0.5 ** (days_elapsed / half_life)

    expiration_penalty = 0.01 if entry.is_expired else 1.0
    access_bonus = log(entry.access_count + 1) * ACCESS_BONUS_FACTOR

    theoretical_priority = min(
        1.0,
        max(0.0, entry.importance * time_factor * expiration_penalty + access_bonus),
    )

    # 距遗忘天数估算
    days_to_forgotten: int | None = None
    if half_life and entry.importance > 0 and not entry.is_permanent:
        try:
            # importance * 0.5^(x/half_life) < 0.05  =>  x > half_life * log2(importance/0.05)
            threshold_days = half_life * (log(entry.importance / 0.05) / log(2))
            days_to_forgotten = max(0, int(threshold_days - days_elapsed))
        except (ValueError, ZeroDivisionError):
            days_to_forgotten = None

    state = "ACTIVE" if entry.is_permanent else DecayState.from_priority(theoretical_priority).value.upper()

    return DecayResult(
        memory_id=memory_id,
        days_elapsed=days_elapsed,
        half_life_days=half_life,
        time_factor=round(time_factor, 4),
        expiration_penalty=expiration_penalty,
        access_bonus=round(access_bonus, 4),
        theoretical_priority=round(theoretical_priority, 4),
        days_to_forgotten=days_to_forgotten,
        current_state=state,
    )


def make_time_decay_calculator_tool(memory_store: SqliteMemoryStore):
    """创建 time_decay_calculator LangChain Tool."""

    @tool
    async def time_decay_calculator(memory_id: str) -> dict[str, Any]:
        """计算给定记忆的时间衰减状态, 返回理论优先级和各维度分解值.

        这是纯公式计算, 不包含 Agent 的多维度评估.
        Agent 应在此基线值之上执行 CoT 分析（考虑访问频率、情绪强度、关联性等）.

        Args:
            memory_id: 记忆条目的唯一 ID

        Returns:
            {memory_id, days_elapsed, half_life_days, time_factor,
             expiration_penalty, access_bonus, theoretical_priority,
             days_to_forgotten, current_state}
            若记忆不存在, 返回 {"error": "memory not found"}.
        """
        result = await calculate_decay(memory_store, memory_id)
        if result is None:
            return {"error": "memory not found", "memory_id": memory_id}
        return result.to_dict()

    return time_decay_calculator
