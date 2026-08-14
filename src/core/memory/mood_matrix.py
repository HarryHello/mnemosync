"""好感度 × 情绪 6×6 状态引导矩阵 (v0.4.1, RFC §5).

- 配置化: 复用提示词两层存储 (defaults + data/prompts 覆盖), 面板提示词页统一管理
- 每格一段纯文本 (无标签模板), 标题格式 `## <好感度档>_<心情段>`
- **覆盖合并**: 覆盖文件可只写要改的格子, 读取时 defaults 36 格打底 + override 合并
- 缺格/解析失败回退默认格 (不存在时返回空串, 调用方降级为不注入)
"""

from __future__ import annotations

import logging
import re

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
