"""Shared helper utilities for graph nodes.

Pure functions and lightweight helpers used across multiple node implementations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.memory.audience import RetrievalContext
from src.core.utils import last_user_message
from src.infra.forwarder.multi import MultiForwarder

if TYPE_CHECKING:
    from src.core.graph.state import AgentState

logger = logging.getLogger(__name__)


def _format_emotion_text(emotion_analysis: dict) -> str:
    """Format emotion analysis dict into human-readable text for agents."""
    return (
        f"情绪: {emotion_analysis.get('emotion', 'neutral')}, "
        f"强度: {emotion_analysis.get('intensity', 0.0):.2f}, "
        f"类别: {emotion_analysis.get('category', 'other')}"
    )


async def _compute_emotion(
    forwarder: MultiForwarder,
    extracted: list[dict],
) -> dict:
    """Pre-compute emotion analysis, shared across agents."""
    text = last_user_message(extracted)
    if not text:
        return {"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""}
    try:
        from src.tools.emotion_analyzer import analyze_emotion
        result = await analyze_emotion(forwarder, text)
        return result.to_dict()
    except Exception as e:
        logger.warning("情绪分析失败: %s", e)
        return {"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""}


def _resolve_addressing(rel, settings) -> tuple[str, str, str]:
    """Runtime addressing resolution: table values (non-None) take priority, else fallback to TOML baseline.

    v0.2.10 onwards relationships table has persona_addressing / user_addressing / context columns.
    """
    base = settings.persona.relation
    if rel is None:
        return base.persona_addressing, base.user_addressing, base.context
    return (
        rel.persona_addressing or base.persona_addressing,
        rel.user_addressing or base.user_addressing,
        rel.context or base.context,
    )


def _retrieval_context(state: AgentState, rel=None) -> RetrievalContext:
    """Build audience context from graph state (v0.3.0).

    rel is used for FRIENDS_ONLY / CONFIDENTIAL threshold checks;
    callers should load the relationship before retrieval.
    """
    return RetrievalContext(
        effective_user_id=state.get("source_user") or None,
        actor_id=state.get("actor_id"),
        space_id=state.get("space_id"),
        channel_type=state.get("channel_type"),
        relationship=rel,
    )
