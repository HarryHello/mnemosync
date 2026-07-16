"""测试 src/api/reasoning_control.py 的决策与合成逻辑.

覆盖 4 条决策规则的真值表 + 前缀匹配 + 自适应缓存 + SSE 帧合成。
不接触 Forwarder / LangGraph, 纯逻辑测试。
"""

from __future__ import annotations

import json

import pytest

from src.api.reasoning_control import (
    build_reasoning_stream_frames,
    chunk_has_native_reasoning,
    clear_native_cache,
    is_native_cached,
    is_native_reasoning_model,
    mark_native_reasoning,
    should_use_proxy_thinking,
)
from src.api.schemas.forward import ChatCompletionRequest, ChatMessage
from src.core.config import (
    DEFAULT_NATIVE_REASONING_MODELS,
    GraphConfig,
    Settings,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    clear_native_cache()
    yield
    clear_native_cache()


def _mk_settings(*, default: bool = False, patterns: list[str] | None = None) -> Settings:
    return Settings(
        graph=GraphConfig(
            proxy_thinking_default=default,
            proxy_thinking_native_reasoning_models=patterns
            if patterns is not None
            else list(DEFAULT_NATIVE_REASONING_MODELS),
        ),
    )


def _mk_request(**overrides) -> ChatCompletionRequest:
    payload = {
        "model": "mnemosync-any",
        "messages": [ChatMessage(role="user", content="hi")],
    }
    payload.update(overrides)
    return ChatCompletionRequest(**payload)


# ─── 前缀匹配 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model, pattern, expected",
    [
        ("deepseek-r1-distill-llama-70b", "deepseek-r1*", True),
        ("deepseek-r1", "deepseek-r1*", True),
        ("deepseek-v3", "deepseek-r1*", False),
        ("o1-preview", "o1*", True),
        ("o3-mini", "o3*", True),
        ("gpt-5-thinking-lite", "gpt-5-thinking-*", True),
        ("qwq-32b-preview", "qwq*", True),
        ("qwen-max", "o1*", False),
        ("deepseek-r1", "deepseek-reasoner", False),
        ("deepseek-reasoner", "deepseek-reasoner", True),  # 精确匹配无 *
    ],
)
def test_pattern_prefix_match(model, pattern, expected):
    assert is_native_reasoning_model(model, [pattern]) is expected


def test_pattern_empty_model_never_matches():
    assert is_native_reasoning_model("", ["*"]) is False


# ─── 自适应缓存 ───────────────────────────────────────────────


def test_mark_and_cache_hit():
    assert not is_native_cached("some-new-model")
    mark_native_reasoning("some-new-model")
    assert is_native_cached("some-new-model")


def test_mark_empty_is_noop():
    mark_native_reasoning("")
    assert not is_native_cached("")


def test_clear_cache_resets():
    mark_native_reasoning("m")
    assert is_native_cached("m")
    clear_native_cache()
    assert not is_native_cached("m")


# ─── 4 条决策规则的真值表 ─────────────────────────────────────


def test_rule1_tools_skip():
    """规则 1: 有 tools → skip."""
    req = _mk_request(tools=[{"type": "function", "function": {"name": "x"}}])
    assert should_use_proxy_thinking(req, _mk_settings(default=True), "qwen-max") is False


def test_rule2_native_pattern_skip():
    """规则 2: 前缀命中 → skip, 即使前台带 reasoning_effort."""
    req = _mk_request(reasoning_effort="high")
    assert should_use_proxy_thinking(req, _mk_settings(), "deepseek-r1-distill") is False


def test_rule2_native_cache_skip():
    """规则 2: 缓存命中 → skip."""
    mark_native_reasoning("mystery-model-v2")
    req = _mk_request(reasoning_effort="high")
    assert should_use_proxy_thinking(req, _mk_settings(), "mystery-model-v2") is False


def test_rule3_frontend_hint_enables_when_no_native():
    """规则 3: 前台点名要推理 + 主模型无原生 → enable (核心语义)."""
    req = _mk_request(reasoning_effort="medium")
    assert should_use_proxy_thinking(req, _mk_settings(default=False), "qwen-max") is True


def test_rule3_thinking_field_also_triggers():
    req = _mk_request(thinking={"type": "enabled", "budget_tokens": 1024})
    assert should_use_proxy_thinking(req, _mk_settings(default=False), "qwen-max") is True


def test_rule3_reasoning_dict_also_triggers():
    req = _mk_request(reasoning={"effort": "high"})
    assert should_use_proxy_thinking(req, _mk_settings(default=False), "qwen-max") is True


def test_rule4_default_true_enables_without_hint():
    """规则 4: 无 tools / 无原生 / 无前台提示 → 走 proxy_thinking_default."""
    req = _mk_request()
    assert should_use_proxy_thinking(req, _mk_settings(default=True), "qwen-max") is True


def test_rule4_default_false_skips():
    req = _mk_request()
    assert should_use_proxy_thinking(req, _mk_settings(default=False), "qwen-max") is False


def test_tools_beats_frontend_hint():
    """规则 1 优先于规则 3: 即使前台要推理, 只要有 tools 就 skip."""
    req = _mk_request(
        tools=[{"type": "function", "function": {"name": "x"}}],
        reasoning_effort="high",
    )
    assert should_use_proxy_thinking(req, _mk_settings(default=True), "qwen-max") is False


# ─── SSE 帧合成 ───────────────────────────────────────────────


def test_build_frames_empty_reasoning_returns_empty():
    assert build_reasoning_stream_frames("", chatcmpl_id="id", model="m") == []


def test_build_frames_first_is_role_assistant():
    frames = build_reasoning_stream_frames("hello", chatcmpl_id="id", model="m")
    assert len(frames) >= 2  # role 帧 + 至少一段推理帧
    first = json.loads(frames[0].removeprefix(b"data: ").decode().strip())
    assert first["choices"][0]["delta"] == {"role": "assistant", "reasoning_content": ""}
    assert first["id"] == "id"
    assert first["model"] == "m"
    assert first["object"] == "chat.completion.chunk"


def test_build_frames_reassembles_to_original_text():
    text = "第一段思考。\n第二段更长的思考内容。" * 10
    frames = build_reasoning_stream_frames(text, chatcmpl_id="id", model="m")
    reassembled = ""
    for f in frames[1:]:  # 跳过 role 帧
        payload = json.loads(f.removeprefix(b"data: ").decode().strip())
        reassembled += payload["choices"][0]["delta"].get("reasoning_content", "")
    assert reassembled == text


def test_build_frames_no_finish_reason():
    frames = build_reasoning_stream_frames("x", chatcmpl_id="id", model="m")
    for f in frames:
        payload = json.loads(f.removeprefix(b"data: ").decode().strip())
        assert payload["choices"][0]["finish_reason"] is None


# ─── chunk 探测 ──────────────────────────────────────────────


def test_chunk_probe_detects_reasoning_field():
    chunk = b'data: {"choices":[{"delta":{"reasoning_content":"..."}}]}\n\n'
    assert chunk_has_native_reasoning(chunk)


def test_chunk_probe_ignores_normal_content():
    chunk = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    assert not chunk_has_native_reasoning(chunk)


def test_chunk_probe_empty_bytes():
    assert not chunk_has_native_reasoning(b"")
