"""跨前端对话流水的短期记忆装填 (v0.2.6).

服务器把所有前端的对话汇聚成同一条连续流. 装填时用双窗口:
  * 时间窗 (settings.storage.short_term_days, 默认 7d): 硬边界 - 老于此的
    不再考虑, 由后台清理任务定期删掉.
  * 模型窗 (ResolvedCandidate.context_length): 软预算 - 从最老那端往新
    裁剪, 直到本轮 (system + history + new_user + reserve_output) 不超过.

Token 估算走保守混合中英启发式: 每 2 字符 ≈ 1 token, 再加 8 tokens 的
role/结构 overhead. 精确性不重要, 有个上限就够避免上游 4001. 真正的 tokenizer
在换模型时形变太大, 且要跟着上游动 — 不值当在中间件里维护。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.persistence.conversation_store import ConversationTurn

logger = logging.getLogger(__name__)


# 每条 OpenAI message 的结构开销 (role + 分隔), 保守取 8 tokens
_PER_MESSAGE_OVERHEAD = 8

# 模型 context_length 缺失时的兜底 (最保守假设)
DEFAULT_CONTEXT_LENGTH_FALLBACK = 8192

# 应答保留区: 至少 512, 至多 context/4, 优先取用户的 max_tokens
_MIN_RESERVE_FOR_OUTPUT = 512


def estimate_tokens(text: str) -> int:
    """混合中英文的保守估算. len // 2 是中文极限, 英文/代码会低估;
    加 +8 message overhead 是为了给结构留余量。返回值只用于预算判断,
    不落库 (落库时用 len(text) // 2 更简单, 见 append_turn)."""
    if not text:
        return _PER_MESSAGE_OVERHEAD
    return len(text) // 2 + _PER_MESSAGE_OVERHEAD


def token_count_for_storage(text: str) -> int:
    """写入 conversation_turns.token_count 时用. 与估算保持一致口径."""
    return estimate_tokens(text)


@dataclass
class BuiltContext:
    """装填结果 + 观测字段."""

    conversation_history: list[dict[str, Any]]  # 供 build_main_dialogue_messages 用
    total_candidates: int  # 时间窗内候选数
    kept: int  # 实际保留条数
    dropped_by_budget: int  # 因模型窗预算被丢弃的条数
    budget: int  # 本轮可分配给 history 的 token 预算
    used: int  # 保留 history 实际估计 tokens
    active_participants: list[str]  # 裁剪后历史中最近出现的参与者（模型可读）


def _resolve_context_budget(
    context_length: int | None,
    system_text: str,
    new_user_text: str,
    max_tokens_hint: int | None,
) -> int:
    """算出 history 可分配的 token 预算.

    预算 = ctx - system - new_user - reserve_output
    reserve_output 优先取用户 max_tokens; 缺失则 min(4096, ctx // 4), 下限 512.
    """
    ctx = context_length or DEFAULT_CONTEXT_LENGTH_FALLBACK
    if context_length is None:
        logger.warning(
            "context_length 未设置, 使用兜底 %d — 强烈建议在面板给 MAIN 角色的"
            "候选填 context_length, 否则记忆装填会被过度裁剪或误判溢出",
            DEFAULT_CONTEXT_LENGTH_FALLBACK,
        )
    reserve = max_tokens_hint if max_tokens_hint else min(4096, max(ctx // 4, _MIN_RESERVE_FOR_OUTPUT))
    reserve = max(reserve, _MIN_RESERVE_FOR_OUTPUT)
    budget = ctx - estimate_tokens(system_text) - estimate_tokens(new_user_text) - reserve
    return max(budget, 0)


def _turn_identity(turn: ConversationTurn) -> str | None:
    """返回模型可读且可消歧的说话者标签；绝不退化为内部 UUID."""
    if not (turn.display_name_snapshot or turn.external_key_snapshot):
        return None
    identity = turn.display_name_snapshot or "unknown"
    if turn.external_key_snapshot:
        identity = f"{identity} | {turn.source_frontend or 'unknown'} {turn.external_key_snapshot}"
    return identity


def _turn_to_message(turn: ConversationTurn) -> dict[str, Any]:
    content = turn.content
    identity = _turn_identity(turn)
    if turn.role == "user" and identity:
        content = f"[{identity}]: {content}"
    return {"role": turn.role, "content": content}


def _active_participants(turns: list[ConversationTurn], limit: int = 12) -> list[str]:
    """按最近出现顺序去重参与者，再恢复为自然的时间顺序."""
    seen: set[str] = set()
    recent: list[str] = []
    for turn in reversed(turns):
        if turn.role != "user":
            continue
        identity = _turn_identity(turn)
        if not identity or identity.casefold() in seen:
            continue
        seen.add(identity.casefold())
        recent.append(identity)
        if len(recent) >= limit:
            break
    return list(reversed(recent))


def trim_by_budget(
    turns: list[ConversationTurn], budget: int
) -> tuple[list[ConversationTurn], int, int]:
    """从最老那端往新裁剪, 直到累计 token 不超过 budget.

    保留末端 (最近) 的对话, 因为它们与"当前一句"最相关. 头尾裁剪允许
    history 以 user 或 assistant 起头, 上游模型对此都能吃 — 不再强行成对。

    Returns:
        (kept_turns, kept_tokens, dropped_count)
    """
    if budget <= 0 or not turns:
        return [], 0, len(turns)

    # 从最新开始往前 accumulate, 满了就停
    kept_rev: list[ConversationTurn] = []
    used = 0
    for turn in reversed(turns):
        cost = turn.token_count or estimate_tokens(turn.content)
        if used + cost > budget:
            break
        kept_rev.append(turn)
        used += cost
    kept = list(reversed(kept_rev))
    return kept, used, len(turns) - len(kept)


async def build_short_term_history(
    store,  # SqliteConversationStore, 松耦合避免循环导入
    now: datetime,
    window_days: int,
    context_length: int | None,
    system_text: str,
    new_user_text: str,
    max_tokens_hint: int | None,
    space_id: str | None = None,
    source_user: str | None = None,
) -> BuiltContext:
    """跨前端对话流水 → 主对话 history.

    Args:
        store: SqliteConversationStore
        now: 当前 UTC 时间 (方便测试注入)
        window_days: 时间窗宽度
        context_length: 上游模型 context_length (来自 ResolvedCandidate)
        system_text: 已拼装好的 system 内容 (用来算已占 tokens)
        new_user_text: 本轮新用户消息
        max_tokens_hint: 客户端 max_tokens (用来定应答保留区)
        space_id: 会话空间 ID (v0.3.0). 非空时只装填本空间流水 —
            群聊上下文绝不能混入其他空间 (别的群/私聊) 的对话;
            为空时退化为全局跨前端流水 (单用户私聊场景).
        source_user: 有效用户 ID. 用于 space_id 为空时按用户隔离对话历史,
            防止不同用户的上下文混杂.
    """
    since = now - timedelta(days=window_days)
    if space_id:
        candidates = await store.list_for_space(space_id, since=since, limit=5000)
    elif source_user:
        candidates = await store.list_since_for_user(source_user, since=since, limit=5000)
    else:
        # 未归属且无空间: 不加载任何对话历史 (安全兜底)
        candidates = []
    budget = _resolve_context_budget(context_length, system_text, new_user_text, max_tokens_hint)
    kept, used, dropped = trim_by_budget(candidates, budget)
    return BuiltContext(
        conversation_history=[_turn_to_message(t) for t in kept],
        total_candidates=len(candidates),
        kept=len(kept),
        dropped_by_budget=dropped,
        budget=budget,
        used=used,
        active_participants=_active_participants(kept),
    )
