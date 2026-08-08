"""Vision Agent 辅助函数测试.

覆盖 extract_image_parts / has_image_parts / strip_image_parts.
"""

from __future__ import annotations

from src.core.agents.vision import (
    extract_image_parts,
    has_image_parts,
    strip_image_parts,
)


def test_extract_image_parts_from_mixed_content() -> None:
    content = [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
    ]
    parts = extract_image_parts(content)
    assert len(parts) == 2
    assert all(p["type"] == "image_url" for p in parts)


def test_extract_image_parts_from_text_only() -> None:
    content = [{"type": "text", "text": "hello"}]
    assert extract_image_parts(content) == []


def test_extract_image_parts_from_string() -> None:
    assert extract_image_parts("plain text") == []


def test_extract_image_parts_from_none() -> None:
    assert extract_image_parts(None) == []


def test_has_image_parts_true() -> None:
    content = [{"type": "image_url", "image_url": {"url": "https://x.com/a.png"}}]
    assert has_image_parts(content) is True


def test_has_image_parts_false_for_text() -> None:
    assert has_image_parts([{"type": "text", "text": "hi"}]) is False
    assert has_image_parts("just a string") is False
    assert has_image_parts(None) is False


def test_strip_image_parts_returns_text() -> None:
    content = [
        {"type": "text", "text": "line1"},
        {"type": "image_url", "image_url": {"url": "https://x.com/a.png"}},
        {"type": "text", "text": "line2"},
    ]
    assert strip_image_parts(content) == "line1\nline2"


def test_strip_image_parts_string_passthrough() -> None:
    assert strip_image_parts("already text") == "already text"


def test_strip_image_parts_only_images() -> None:
    content = [{"type": "image_url", "image_url": {"url": "https://x.com/a.png"}}]
    assert strip_image_parts(content) == ""
