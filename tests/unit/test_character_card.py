"""角色卡导入测试."""

import json

import pytest
from src.infra.character_card import (
    CharacterCard,
    CharacterCardError,
    _sanitize_data,
    _validate_json_depth,
    _validate_string_length,
    create_persona_definition,
    map_to_persona,
)


def test_json_card_parsing():
    data = {
        "name": "绫音",
        "description": "一个沉默寡言的高中生",
        "personality": "冷淡、短句、少用语气词",
        "scenario": "和哥哥同住",
        "first_mes": "你好",
        "system_prompt": "你叫绫音",
    }
    card = CharacterCard(data, "json")
    assert card.name == "绫音"
    assert card.description == "一个沉默寡言的高中生"
    assert card.personality == "冷淡、短句、少用语气词"
    assert card.scenario == "和哥哥同住"
    assert card.first_mes == "你好"
    assert card.system_prompt == "你叫绫音"


def test_map_to_persona():
    card = CharacterCard({
        "name": "绫音",
        "description": "沉默寡言的高中生",
        "personality": "冷淡、短句",
        "post_history_instructions": "和哥哥同住一个公寓",
        "scenario": "目前正在准备考试",
    }, "json")
    identity = map_to_persona(card)
    assert "沉默寡言的高中生" in identity["personality"]
    assert identity["speaking_style"] == "冷淡、短句"
    assert "和哥哥同住一个公寓" in identity["context"]
    assert "正在准备考试" in identity["context"]
    assert identity["persona_addressing"] == "绫音"


def test_create_persona_definition():
    card = CharacterCard({"name": "测试角色"}, "json")
    result = create_persona_definition(card)
    assert result["changelog"].startswith("从角色卡导入: 测试角色")
    assert "personality" in result["identity"]
    assert result["identity"]["persona_addressing"] == "测试角色"


def test_sanitize_removes_executable():
    data = {"name": "test", "javascript": "alert(1)", "code": "evil"}
    result = _sanitize_data(data)
    assert "javascript" not in result
    assert "code" not in result
    assert result["name"] == "test"


def test_validate_json_depth_too_deep():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": {"k": "deep"}}}}}}}}}}}
    with pytest.raises(CharacterCardError):
        _validate_json_depth(deep)


def test_validate_string_length_too_long():
    with pytest.raises(CharacterCardError):
        _validate_string_length("x" * 10001)


def test_card_with_character_book():
    card = CharacterCard({
        "name": "绫音",
        "character_book": {"entries": [{"keywords": ["测试"], "content": "测试知识"}]},
    }, "json")
    assert card.character_book is not None
    assert card.character_book["entries"][0]["keywords"] == ["测试"]


@pytest.mark.asyncio
async def test_png_v1_parse():
    """模拟 V1 格式: JSON 追加在 PNG IEND 后."""
    # 构造一个最小 PNG (89x1 像素灰度, 仅头+IEND)
    import struct
    import zlib

    width, height = 1, 1
    # 创建最小 PNG
    raw = b"\x89PNG\r\n\x1a\n"  # signature
    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    raw += struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    # IDAT chunk (minimal)
    compressed = zlib.compress(b"\x00\x80\x00\x00")
    idat_crc = zlib.crc32(b"IDAT" + compressed)
    raw += struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    # IEND chunk
    raw += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)

    # 追加 V1 元数据 (JSON)
    metadata = json.dumps({"name": "绫音", "description": "测试角色"}, ensure_ascii=False)
    raw += str(len(metadata)).encode("ascii") + metadata.encode("utf-8")

    from src.infra.character_card import parse_png
    result = parse_png(raw)
    assert result is not None
    assert result["name"] == "绫音"
    assert result["description"] == "测试角色"
