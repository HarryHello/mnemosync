"""测试 parse_sse_stream — 从 chunk 列表还原 assistant 内容.

这个函数在流式路径的后台记忆图里用来把 SSE 拼回文本, 一旦出错整个
后台入库/关系分析就基于错文本运行, 值得单测。
"""

from __future__ import annotations

from src.infra.forwarder.forwarder import parse_sse_stream


def _chunks(*payloads: str) -> list[bytes]:
    return [f"data: {p}\n\n".encode() for p in payloads]


def test_extracts_content_deltas_in_order():
    chunks = _chunks(
        '{"choices":[{"delta":{"role":"assistant","content":"你"}}]}',
        '{"choices":[{"delta":{"content":"好"}}]}',
        '{"choices":[{"delta":{"content":"世界"}}]}',
        "[DONE]",
    )
    assert parse_sse_stream(chunks) == "你好世界"


def test_ignores_role_only_and_empty_delta():
    chunks = _chunks(
        '{"choices":[{"delta":{"role":"assistant"}}]}',
        '{"choices":[{"delta":{}}]}',
        '{"choices":[{"delta":{"content":"x"}}]}',
    )
    assert parse_sse_stream(chunks) == "x"


def test_ignores_reasoning_content():
    """代理推理合成的 reasoning_content 帧不应该被拼进主对话文本."""
    chunks = _chunks(
        '{"choices":[{"delta":{"reasoning_content":"想一想..."}}]}',
        '{"choices":[{"delta":{"content":"答案"}}]}',
    )
    assert parse_sse_stream(chunks) == "答案"


def test_handles_multiple_data_lines_in_one_chunk():
    combined = (
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
    )
    assert parse_sse_stream([combined]) == "ab"


def test_survives_malformed_json():
    chunks = [
        b'data: {"choices":[{"delta":{"content":"good"}}]}\n\n',
        b"data: not-json\n\n",
        b'data: {"choices":[{"delta":{"content":"still-good"}}]}\n\n',
    ]
    assert parse_sse_stream(chunks) == "goodstill-good"


def test_empty_input():
    assert parse_sse_stream([]) == ""


def test_only_done_marker():
    assert parse_sse_stream(_chunks("[DONE]")) == ""
