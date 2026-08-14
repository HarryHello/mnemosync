"""好感度 × 情绪 6×6 状态引导矩阵 (v0.4.1, RFC §5).

- 配置化: 复用提示词两层存储 (defaults + data/prompts 覆盖), 面板提示词页统一管理
- 每格一段纯文本 (无标签模板), 标题格式 `## <好感度档>_<心情段>`
- **覆盖合并**: 覆盖文件可只写要改的格子, 读取时 defaults 36 格打底 + override 合并
- 缺格/解析失败回退默认格 (不存在时返回空串, 调用方降级为不注入)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.core.memory.models import RELATIONSHIP_TIERS
from src.core.memory.mood import MOOD_TIERS
from src.core.prompts import get_prompt_store

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^##\s+(\S+)\s*$", re.MULTILINE)

#: 好感度档名 (与 RELATIONSHIP_TIERS 对齐)
FAVOR_TIERS: tuple[str, ...] = tuple(t[0] for t in RELATIONSHIP_TIERS)
#: 心情段名 (与 MOOD_TIERS 对齐)
MOOD_LABELS: tuple[str, ...] = tuple(t[0] for t in MOOD_TIERS)

#: 每格 id 形如 "hostile_心情极差"
CELL_IDS: tuple[str, ...] = tuple(
    f"{f}_{m}" for f in FAVOR_TIERS for m in MOOD_LABELS
)


def _parse_cells(text: str) -> dict[str, str]:
    """解析矩阵文本: `## id\n内容...` → {id: 内容}. 非法 id 丢弃."""
    cells: dict[str, str] = {}
    lines = text.splitlines()
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            if current is not None and current in CELL_IDS and buf:
                cells[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None and current in CELL_IDS and buf:
        cells[current] = "\n".join(buf).strip()
    return cells


def load_mood_matrix() -> dict[str, str]:
    """加载矩阵: defaults 36 格打底 + override 合并 (只覆盖存在的格子)."""
    store = get_prompt_store()
    cells: dict[str, str] = {}
    try:
        default_text = store.load_default("mood_matrix")
        cells.update(_parse_cells(default_text))
    except (FileNotFoundError, OSError) as e:
        logger.warning("mood_matrix 默认文件缺失: %s", e)
    try:
        override_text = store.load_override("mood_matrix")
        if override_text:
            cells.update(_parse_cells(override_text))
    except OSError as e:
        logger.warning("mood_matrix 覆盖文件读取失败: %s", e)
    return cells


def get_mood_cell(favor_tier: str, mood_label: str, cells: dict[str, str] | None = None) -> str:
    """取一格引导文本; 缺格回退空串 (调用方降级为不注入)."""
    if cells is None:
        cells = load_mood_matrix()
    return cells.get(f"{favor_tier}_{mood_label}", "")


def build_persona_state_section(
    *,
    favor_tier: str,
    mood_label: str,
    cells: dict[str, str] | None = None,
) -> str:
    """构建"人格当前状态"注入段 (RFC §5.3).

    只注入阶段标签与引导文本, 不注入连续数值 (防模型"演数值").
    引导文本为空时只输出状态行.
    """
    guide = get_mood_cell(favor_tier, mood_label, cells)
    head = f"[人格状态] 对当前发言者: {favor_tier} / 心情: {mood_label}"
    if guide:
        return head + "\n" + guide
    return head


def build_state_section(
    *,
    favor_tier: str,
    mood_state: dict[str, Any] | None,
    anchor_text: str = "",
) -> str:
    """完整"人格当前状态"段 (非流式/流式共用): 矩阵格 + cause + 锚点.

    - mood_state 为 None (通道降级) 时仅注入锚点 (若有)
    - cause 是 public 脱敏文本; anchor_text 已由调用方按 subject 加载
    """
    section = ""
    if mood_state is not None:
        mood_label = mood_state.get("tier") or "心情不错"
        section = build_persona_state_section(favor_tier=favor_tier, mood_label=mood_label)
        cause = mood_state.get("cause")
        if cause:
            section += "\n心情缘由：" + str(cause)
    if anchor_text:
        section = section + "\n" + anchor_text if section else anchor_text
    return section
# ── 覆盖层编辑 (面板 6×6 grid, v0.4.1) ──────────────────────────

#: 单格文本长度上限 (面板编辑校验)
CELL_TEXT_MAX_LENGTH = 2000


def _override_path() -> Path:
    """覆盖文件路径 (与 PromptStore 同目录, data/prompts/mood_matrix.md)."""
    from src.core.config import get_settings

    return Path(get_settings().storage.prompts_override_dir_abs) / "mood_matrix.md"


def load_override_cells() -> dict[str, str]:
    """读取覆盖层格子 (无覆盖/文件缺失 → 空 dict)."""
    path = _override_path()
    if not path.is_file():
        return {}
    try:
        body, _ = get_prompt_store()._strip_frontmatter(path.read_text(encoding="utf-8"))
    except OSError as e:
        logger.warning("mood_matrix 覆盖文件读取失败: %s", e)
        return {}
    return _parse_cells(body)


def _replace_cell_in_text(text: str, cell_id: str, new_content: str) -> str:
    """替换/新增标题块, 保留其余内容 (含 frontmatter)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        m = _HEADING_RE.match(lines[i])
        if m and m.group(1) == cell_id:
            i += 1
            while i < len(lines) and not _HEADING_RE.match(lines[i]):
                i += 1
            out.append(f"## {cell_id}")
            out.append("")
            out.extend(new_content.splitlines())
            out.append("")
            replaced = True
            continue
        out.append(lines[i])
        i += 1
    if not replaced:
        out.append(f"## {cell_id}")
        out.append("")
        out.extend(new_content.splitlines())
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _remove_cell_from_text(text: str, cell_id: str) -> str:
    """删除标题块; 未找到时返回原文本."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    found = False
    while i < len(lines):
        m = _HEADING_RE.match(lines[i])
        if m and m.group(1) == cell_id:
            found = True
            i += 1
            while i < len(lines) and not _HEADING_RE.match(lines[i]):
                i += 1
            # 去掉块尾多余空行
            while out and out[-1] == "":
                out.pop()
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip() + "\n" if found else text


_OVERRIDE_HEADER = (
    "---\n"
    "title: mood matrix override\n"
    "description: only edited cells, defaults merged on load.\n"
    "---\n"
)


def save_cell(cell_id: str, text: str) -> None:
    """保存单个格子到覆盖层 (面板 grid 编辑).

    - cell_id 必须在白名单 (CELL_IDS), 防路径/标题注入
    - 空文本 = 重置该格 (回默认)
    - 其余格子原样保留 (覆盖合并)
    """
    if cell_id not in CELL_IDS:
        raise ValueError(f"非法格子 id: {cell_id!r}")
    text = (text or "").strip()
    if not text:
        reset_cell(cell_id)
        return
    if len(text) > CELL_TEXT_MAX_LENGTH:
        raise ValueError(f"文本过长 (最多 {CELL_TEXT_MAX_LENGTH} 字符)")
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        body = path.read_text(encoding="utf-8")
    else:
        body = _OVERRIDE_HEADER
    new_body = _replace_cell_in_text(body, cell_id, text)
    path.write_text(new_body, encoding="utf-8")


def reset_cell(cell_id: str) -> bool:
    """从覆盖层移除该格 (回默认); 无覆盖/格不在时返回 False."""
    if cell_id not in CELL_IDS:
        raise ValueError(f"非法格子 id: {cell_id!r}")
    path = _override_path()
    if not path.is_file():
        return False
    body = path.read_text(encoding="utf-8")
    new_body = _remove_cell_from_text(body, cell_id)
    if new_body == body:
        return False
    # 无任何格子 → 删除覆盖文件 (完全回默认)
    stripped, _ = get_prompt_store()._strip_frontmatter(new_body)
    if not _parse_cells(stripped):
        path.unlink()
    else:
        path.write_text(new_body, encoding="utf-8")
    return True

