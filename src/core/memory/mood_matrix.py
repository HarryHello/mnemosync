"""好感度 × 情绪 6×6 状态引导矩阵 (v0.4.1, RFC §5).

- 每格一个 markdown 文件 (v0.4.1 存储演进): 单格编辑/读取只碰对应文件,
  不做整文件解析重写. 默认层随包发布, 覆盖层按需单文件.
- 标签全英文: 格子 id 形如 "hostile_dreadful" (好感度档_心情段);
  中文显示经 MOOD_LABELS_ZH / RELATIONSHIP_STAGE_LABELS 映射.
- 覆盖合并语义: override 目录存在该格文件即覆盖, 否则用默认.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.core.memory.models import RELATIONSHIP_TIERS
from src.core.memory.mood import MOOD_TIERS, mood_label_zh

logger = logging.getLogger(__name__)

#: 好感度档名 (与 RELATIONSHIP_TIERS 对齐)
FAVOR_TIERS: tuple[str, ...] = tuple(t[0] for t in RELATIONSHIP_TIERS)
#: 心情段名 (与 MOOD_TIERS 对齐, 全英文)
MOOD_LABELS: tuple[str, ...] = tuple(t[0] for t in MOOD_TIERS)

#: 每格 id 形如 "hostile_dreadful"
CELL_IDS: tuple[str, ...] = tuple(
    f"{f}_{m}" for f in FAVOR_TIERS for m in MOOD_LABELS
)

#: 单格文本长度上限 (面板编辑校验)
CELL_TEXT_MAX_LENGTH = 2000


def _default_cell_path(cell_id: str) -> Path:
    from src.core.agents import prompts as _prompts_pkg

    return (
        Path(_prompts_pkg.__file__).resolve().parent
        / "defaults" / "mood_matrix" / f"{cell_id}.md"
    )


def _override_cell_path(cell_id: str) -> Path:
    from src.core.config import get_settings

    return (
        Path(get_settings().storage.prompts_override_dir_abs)
        / "mood_matrix" / f"{cell_id}.md"
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def get_cell_text(cell_id: str) -> str:
    """读单格文本 (override 优先, 无则默认; 均无返回空串)."""
    if cell_id not in CELL_IDS:
        return ""
    ov = _override_cell_path(cell_id)
    if ov.is_file():
        return _read_text(ov)
    df = _default_cell_path(cell_id)
    if df.is_file():
        return _read_text(df)
    logger.warning("mood_matrix 默认格子缺失: %s", cell_id)
    return ""


def load_mood_matrix() -> dict[str, str]:
    """加载全矩阵: 36 格, defaults 打底 + override 单文件覆盖."""
    cells: dict[str, str] = {}
    for cell_id in CELL_IDS:
        text = get_cell_text(cell_id)
        if text:
            cells[cell_id] = text
    return cells


def load_override_cells() -> dict[str, str]:
    """读取覆盖层格子 (仅用户编辑过的; 无 → 空 dict)."""
    cells: dict[str, str] = {}
    for cell_id in CELL_IDS:
        p = _override_cell_path(cell_id)
        if p.is_file():
            cells[cell_id] = _read_text(p)
    return cells


def get_mood_cell(favor_tier: str, mood_label: str, cells: dict[str, str] | None = None) -> str:
    """取一格引导文本 (给定 cells 时查表, 否则读单文件); 缺格回退空串."""
    cell_id = f"{favor_tier}_{mood_label}"
    if cells is not None:
        return cells.get(cell_id, "")
    return get_cell_text(cell_id)


def save_cell(cell_id: str, text: str) -> None:
    """保存单个格子 (写覆盖层单文件).

    - cell_id 白名单校验 (防路径注入)
    - 空文本 = 重置该格 (回默认)
    """
    if cell_id not in CELL_IDS:
        raise ValueError(f"非法格子 id: {cell_id!r}")
    text = (text or "").strip()
    if not text:
        reset_cell(cell_id)
        return
    if len(text) > CELL_TEXT_MAX_LENGTH:
        raise ValueError(f"文本过长 (最多 {CELL_TEXT_MAX_LENGTH} 字符)")
    path = _override_cell_path(cell_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def reset_cell(cell_id: str) -> bool:
    """删除覆盖层该格文件 (回默认); 无覆盖时返回 False."""
    if cell_id not in CELL_IDS:
        raise ValueError(f"非法格子 id: {cell_id!r}")
    path = _override_cell_path(cell_id)
    if not path.is_file():
        return False
    path.unlink()
    # 目录空则清理
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True


def build_persona_state_section(
    *,
    favor_tier: str,
    mood_label: str,
    cells: dict[str, str] | None = None,
) -> str:
    """构建"人格当前状态"注入段 (RFC §5.3).

    只注入阶段标签与引导文本, 不注入连续数值 (防模型"演数值").
    心情段显示经中文映射 (模型语境为中文).
    """
    guide = get_mood_cell(favor_tier, mood_label, cells)
    head = f"[人格状态] 对当前发言者: {favor_tier} / 心情: {mood_label_zh(mood_label)}"
    if guide:
        return head + "\n" + guide
    return head


def build_state_section(
    *,
    favor_tier: str,
    mood_state: dict[str, Any] | None,
    anchor_text: str = "",
) -> str:
    """完整"人格当前状态"段 (非流式/流式共用): 矩阵格 + cause + 锚点."""
    section = ""
    if mood_state is not None:
        mood_label = mood_state.get("tier") or "decent"
        section = build_persona_state_section(favor_tier=favor_tier, mood_label=mood_label)
        cause = mood_state.get("cause")
        if cause:
            section += "\n心情缘由：" + str(cause)
    if anchor_text:
        section = section + "\n" + anchor_text if section else anchor_text
    return section
