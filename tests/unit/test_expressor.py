"""Expressor 表达改写 Agent 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.core.agents.factory import ExpressorConfig, run_expressor
from src.infra.forwarder.multi import MultiForwarder


async def test_expressor_disabled_returns_original():
    """禁用时不改写."""
    forwarder = AsyncMock(spec=MultiForwarder)
    cfg = ExpressorConfig(enabled=False)
    result = await run_expressor(
        forwarder, "原始文本", "马达 | astrbot 486394990", "group", "关系", config=cfg,
    )
    assert result == "原始文本"
    forwarder.chat.assert_not_called()


async def test_expressor_short_text_returns_original():
    """短文本不改写."""
    forwarder = AsyncMock(spec=MultiForwarder)
    cfg = ExpressorConfig(enabled=True, min_rewrite_length=10)
    result = await run_expressor(
        forwarder, "你好", "马达 | astrbot 486394990", "group", "关系", config=cfg,
    )
    assert result == "你好"
    forwarder.chat.assert_not_called()


async def test_expressor_rewrites_long_text():
    """长文本被改写."""
    forwarder = AsyncMock(spec=MultiForwarder)
    forwarder.chat = AsyncMock(
        return_value={
            "choices": [{"message": {"content": "好的，这个问题很有意思"}}],
            "usage": {},
        }
    )
    cfg = ExpressorConfig(enabled=True)
    original = (
        "关于你提到的这个问题，我认为有几个方面需要考虑。"
        "首先，从技术实现的角度来看，我们需要确保系统的稳定性。"
        "其次，从用户体验的角度来说，这个改动可能会带来一些不便。"
    )
    result = await run_expressor(
        forwarder, original, "马达 | astrbot 486394990", "group", "关系", config=cfg,
    )
    assert result == "好的，这个问题很有意思"
    assert len(result) < len(original)
    forwarder.chat.assert_called_once()


async def test_expressor_failure_returns_original():
    """改写失败时返回原文."""
    forwarder = AsyncMock(spec=MultiForwarder)
    forwarder.chat = AsyncMock(side_effect=Exception("LLM error"))
    cfg = ExpressorConfig(enabled=True)
    original = "这是一段需要改写的长文本内容"
    result = await run_expressor(
        forwarder, original, "马达 | astrbot 486394990", "group", "关系", config=cfg,
    )
    assert result == original


async def test_expressor_length_bloat_returns_original():
    """改写后长度异常膨胀时返回原文."""
    forwarder = AsyncMock(spec=MultiForwarder)
    forwarder.chat = AsyncMock(
        return_value={
            "choices": [{"message": {"content": "改写后的文本" * 50}}],
            "usage": {},
        }
    )
    cfg = ExpressorConfig(enabled=True)
    original = "这是一段需要改写的长文本内容"
    result = await run_expressor(
        forwarder, original, "马达 | astrbot 486394990", "group", "关系", config=cfg,
    )
    assert result == original
