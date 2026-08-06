"""SillyTavern 角色卡解析.

支持 V1 (JSON appended after IEND) 和 V2 (tEXt chunk) 格式。
字段映射到 PersonaDefinition，含安全校验。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

# 安全限制
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_JSON_DEPTH = 10
MAX_STR_LENGTH = 10000
MAX_IMAGE_PIXELS = 2000 * 2000


class CharacterCardError(Exception):
    """角色卡解析错误."""


class CharacterCard:
    """解析后的角色卡数据."""

    def __init__(self, data: dict[str, Any], source_format: str):
        self.data = data
        self.source_format = source_format  # "v1" | "v2" | "json"

    @property
    def name(self) -> str:
        """角色名称."""
        return cast(str, self.data.get("name", ""))

    @property
    def description(self) -> str:
        """角色描述（最主要的身份陈述）."""
        return cast(str, self.data.get("description", ""))

    @property
    def personality(self) -> str:
        """角色性格（映射为说话风格）."""
        return cast(str, self.data.get("personality", ""))

    @property
    def scenario(self) -> str:
        """初始场景设定."""
        return cast(str, self.data.get("scenario", ""))

    @property
    def first_mes(self) -> str:
        """角色开场白."""
        return cast(str, self.data.get("first_mes", ""))

    @property
    def mes_example(self) -> str:
        """对话示例."""
        return cast(str, self.data.get("mes_example", ""))

    @property
    def system_prompt(self) -> str:
        """系统提示词."""
        return cast(str, self.data.get("system_prompt", ""))

    @property
    def post_history_instructions(self) -> str:
        """历史后置指令（映射为 context）."""
        return cast(str, self.data.get("post_history_instructions", ""))

    @property
    def creator_notes(self) -> str:
        """作者备注."""
        return cast(str, self.data.get("creator_notes", ""))

    @property
    def character_book(self) -> dict[str, Any] | None:
        """角色卡自带的对话书（Lorebook）."""
        return self.data.get("character_book")


def _validate_json_depth(obj: Any, depth: int = 0) -> None:
    """递归校验 JSON 深度."""
    if depth > MAX_JSON_DEPTH:
        raise CharacterCardError(f"JSON depth exceeds {MAX_JSON_DEPTH}")
    if isinstance(obj, dict):
        for v in obj.values():
            _validate_json_depth(v, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _validate_json_depth(item, depth + 1)


def _validate_string_length(obj: Any) -> None:
    """递归校验字符串长度."""
    if isinstance(obj, str):
        if len(obj) > MAX_STR_LENGTH:
            raise CharacterCardError(f"String length {len(obj)} exceeds {MAX_STR_LENGTH}")
    elif isinstance(obj, dict):
        for v in obj.values():
            _validate_string_length(v)
    elif isinstance(obj, list):
        for item in obj:
            _validate_string_length(item)


def _sanitize_data(data: dict[str, Any]) -> dict[str, Any]:
    """安全校验并清理角色卡数据."""
    _validate_json_depth(data)
    _validate_string_length(data)

    # 丢弃可执行字段
    data.pop("javascript", None)
    data.pop("code", None)
    data.pop("eval", None)

    return data


def parse_png(data: bytes) -> dict[str, Any] | None:
    """从 PNG 二进制数据中提取 SillyTavern 角色卡元数据."""
    # 找 IEND chunk: 4 bytes length + "IEND" + 4 bytes CRC
    iend_marker = b"IEND"
    iend_pos = data.find(iend_marker)
    if iend_pos == -1:
        return None

    # IEND 后 4 bytes CRC, 之后就是 JSON 数据
    payload_start = iend_pos + 4 + 4
    payload = data[payload_start:]

    if not payload:
        return None

    # V2: tEXt chunk 中有 "chara" key
    text_chunk = _parse_text_chunk(data)
    if text_chunk:
        return text_chunk

    # V1: JSON 直接追加在 IEND 后, 可能带长度前缀
    return _parse_v1_payload(payload)


def _parse_text_chunk(data: bytes) -> dict[str, Any] | None:
    """从 PNG tEXt chunks 中提取角色卡元数据.

    V2 格式将 JSON 存储在 tEXt chunk 中, key="chara".
    """
    pos = 8  # 跳过 PNG signature
    while pos < len(data):
        if pos + 8 > len(data):
            break
        chunk_len = int.from_bytes(data[pos:pos + 4], "big")
        chunk_type = data[pos + 4:pos + 8]
        chunk_data = data[pos + 8:pos + 8 + chunk_len]

        if chunk_type == b"tEXt" and chunk_len > 0:
            # tEXt 格式: keyword\0text
            null_pos = chunk_data.find(b"\x00")
            if null_pos != -1:
                keyword = chunk_data[:null_pos].decode("latin-1", errors="replace")
                text = chunk_data[null_pos + 1:].decode("latin-1", errors="replace")
                if keyword == "chara":
                    try:
                        return cast(dict[str, Any] | None, json.loads(text))
                    except json.JSONDecodeError:
                        pass

        if chunk_type == b"IEND":
            break
        pos += 4 + 4 + chunk_len + 4

    return None


def _parse_v1_payload(payload: bytes) -> dict[str, Any] | None:
    """尝试多种策略解析 V1 格式的 payload."""
    strategies = [
        _parse_v1_with_length_prefix,
        _parse_v1_direct_json,
    ]
    for strategy in strategies:
        result = strategy(payload)
        if result is not None:
            return result
    return None


def _parse_v1_with_length_prefix(payload: bytes) -> dict[str, Any] | None:
    """尝试带长度前缀的 V1 格式.

    某些版本在 JSON 前有 ASCII 数字表示长度.
    长度前缀是 ASCII, JSON 正文是 UTF-8.
    """
    text_part = payload.decode("latin-1", errors="replace")
    match = re.match(r"^(\d+)", text_part)
    if match:
        json_len = int(match.group(1))
        json_start = match.end()
        # JSON 部分用 UTF-8 解码
        json_bytes = payload[json_start:json_start + json_len]
        try:
            return cast(dict[str, Any] | None, json.loads(json_bytes.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return None


def _parse_v1_direct_json(payload: bytes) -> dict[str, Any] | None:
    """尝试直接解析为 JSON."""
    text = payload.decode("utf-8", errors="replace")
    # 找到第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return cast(dict[str, Any] | None, json.loads(text[start:end + 1]))
        except json.JSONDecodeError:
            pass
    return None


def parse_file(file_path: str) -> CharacterCard:
    """从文件路径解析角色卡."""
    path = Path(file_path)
    if not path.exists():
        raise CharacterCardError(f"File not found: {file_path}")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise CharacterCardError(f"File size exceeds {MAX_FILE_SIZE // (1024*1024)}MB")

    raw = path.read_bytes()

    # 检测文件类型
    if raw[:4] == b"\x89PNG":
        metadata = parse_png(raw)
        if metadata is None:
            raise CharacterCardError("No character metadata found in PNG")
        fmt = "v2" if "spec" in metadata else "v1"
        data = _sanitize_data(metadata)
        return CharacterCard(data, fmt)

    # JSON 文件
    try:
        metadata = json.loads(raw.decode("utf-8"))
        data = _sanitize_data(metadata)
        return CharacterCard(data, "json")
    except json.JSONDecodeError:
        raise CharacterCardError("Not a valid JSON or PNG file")


def map_to_persona(card: CharacterCard) -> dict[str, Any]:
    """将角色卡字段映射到 PersonaDefinition 格式.

    Returns:
        dict 适配 PersonaDefinition.identity 格式
    """
    identity: dict[str, Any] = {
        "personality": "",
        "speaking_style": "",
        "values": [],
        "persona_addressing": card.name or "角色",
        "user_addressing": "用户",
        "context": "",
    }

    # personality: SillyTavern "description" 是最主要的身份描述
    personality_parts = []
    if card.description:
        personality_parts.append(card.description)
    if card.system_prompt:
        personality_parts.append(f"[系统约束]\n{card.system_prompt}")
    identity["personality"] = "\n\n".join(personality_parts)

    # speaking_style: SillyTavern "personality" 字段
    if card.personality:
        identity["speaking_style"] = card.personality

    # context: SillyTavern "post_history_instructions"
    if card.post_history_instructions:
        identity["context"] = card.post_history_instructions

    # scenario: 可以作为 context 的补充
    if card.scenario:
        context = identity["context"]
        if context:
            context += "\n\n" + card.scenario
        else:
            context = card.scenario
        identity["context"] = context

    # creator_notes → values (如果包含核心价值观)
    if card.creator_notes:
        identity["values"] = [card.creator_notes]

    return identity


def create_persona_definition(card: CharacterCard) -> dict[str, Any]:
    """从角色卡创建完整的 PersonaDefinition dict."""
    identity = map_to_persona(card)
    return {
        "identity": identity,
        "space_overrides": {},
        "changelog": f"从角色卡导入: {card.name} ({card.source_format})",
    }
