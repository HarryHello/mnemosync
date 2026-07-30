"""代理推理决策 + 自适应缓存 + 流式合成.

只处理"要不要跑代理推理"以及"怎么把结果塞进 OpenAI 兼容响应"这一层,
不涉及具体推理如何生成 (那是 core/agents/factory.py 的事).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

from src.api.schemas.forward import ChatCompletionRequest
from src.core.config import Settings

logger = logging.getLogger(__name__)


# ── 自适应缓存: 进程内, 遇到上游吐 reasoning_content 就记住 ──────

_native_cache: set[str] = set()


def mark_native_reasoning(model: str) -> None:
    """记录该模型上游会返回 reasoning_content, 下次跳过代理推理."""
    if not model:
        return
    if model in _native_cache:
        return
    _native_cache.add(model)
    logger.info("[proxy_thinking] 自适应: %s 记为具备原生推理, 下次跳过", model)


def is_native_cached(model: str) -> bool:
    return bool(model) and model in _native_cache


def clear_native_cache() -> None:
    _native_cache.clear()


# ── 前缀匹配 ─────────────────────────────────────────────────────


def _matches_pattern(model: str, pattern: str) -> bool:
    """末尾 * 通配, 中间 * 视为字面 (够用)."""
    if not pattern:
        return False
    if pattern.endswith("*"):
        return model.startswith(pattern[:-1])
    return model == pattern


def is_native_reasoning_model(model: str, patterns: Iterable[str]) -> bool:
    if not model:
        return False
    return any(_matches_pattern(model, p) for p in patterns)


# ── 决策 ─────────────────────────────────────────────────────────


def _has_reasoning_body_hint(request: ChatCompletionRequest) -> bool:
    if request.reasoning_effort:
        return True
    if request.reasoning:
        return True
    if request.thinking:
        return True
    return False


def should_use_proxy_thinking(
    request: ChatCompletionRequest,
    settings: Settings,
    main_model: str,
) -> bool:
    """按规则决定是否启用代理推理.

    语义: 代理推理是原生推理的补齐/替代, 不是"用户显式要求就跳过".
      - 主模型具备原生推理 (前缀命中或自适应缓存)     → 让原生接管, skip
      - 主模型不具备原生推理:
          * 前台在请求体点名要推理 (reasoning_effort 等) → 必须补齐, 启用
          * 否则回退 [graph].proxy_thinking_default
      - tools 出现时统一 skip (与推理语义无关的保险位, 且流式路径本就不透传 tools)
    """
    if request.tools:
        logger.debug("[proxy_thinking] skip: 请求带 tools")
        return False

    patterns = settings.graph.proxy_thinking_native_reasoning_models
    native_available = (
        is_native_reasoning_model(main_model, patterns)
        or is_native_cached(main_model)
    )
    if native_available:
        logger.debug("[proxy_thinking] skip: %s 具备原生推理", main_model)
        return False

    if _has_reasoning_body_hint(request):
        logger.debug(
            "[proxy_thinking] enable: 前台点名推理 + 主模型无原生 (%s)", main_model
        )
        return True

    return bool(settings.graph.proxy_thinking_default)


# ── SSE 合成 ─────────────────────────────────────────────────────

_REASONING_CHUNK_SIZE = 40


def _split_reasoning(text: str, size: int = _REASONING_CHUNK_SIZE) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _sse_frame(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def build_reasoning_stream_frames(
    reasoning: str,
    *,
    chatcmpl_id: str,
    model: str,
    created: int | None = None,
) -> list[bytes]:
    """把预推理文本切片成一串 OpenAI-兼容 SSE 帧 (delta.reasoning_content).

    首帧带 role=assistant, 便于严格客户端识别; 无 finish_reason 帧, 由后续
    上游流负责收尾.
    """
    if not reasoning:
        return []
    ts = created if created is not None else int(time.time())
    frames: list[bytes] = []

    def _base_choice(delta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": chatcmpl_id,
            "object": "chat.completion.chunk",
            "created": ts,
            "model": model,
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": None},
            ],
        }

    frames.append(_sse_frame(_base_choice({"role": "assistant", "reasoning_content": ""})))
    for seg in _split_reasoning(reasoning):
        frames.append(_sse_frame(_base_choice({"reasoning_content": seg})))
    return frames


# ── 上游 chunk 感知 (自适应命中) ──────────────────────────────────

_REASONING_MARKER = b'"reasoning_content"'


def chunk_has_native_reasoning(chunk: bytes) -> bool:
    """便宜的 substring 探测, 足够触发缓存写入."""
    if not chunk:
        return False
    return _REASONING_MARKER in chunk


__all__ = [
    "should_use_proxy_thinking",
    "is_native_reasoning_model",
    "is_native_cached",
    "mark_native_reasoning",
    "clear_native_cache",
    "build_reasoning_stream_frames",
    "chunk_has_native_reasoning",
]
