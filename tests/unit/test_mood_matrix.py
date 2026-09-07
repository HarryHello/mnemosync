"""好感度×情绪矩阵单元测试 (v0.4.1, RFC §5).

v0.4.1 存储演进: 每格一个 markdown 文件 (defaults/mood_matrix/<cell>.md +
data/prompts/mood_matrix/<cell>.md 覆盖), 标签全英文.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from src.core.memory import mood_matrix as mm


@pytest.fixture(autouse=True)
def _isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 override 目录 + 提供真实 defaults 目录路径."""
    od = tmp_path / "prompts" / "mood_matrix"
    od.mkdir(parents=True)
    monkeypatch.setattr(mm, "_override_cell_path", lambda cid: od / f"{cid}.md")
    return od


def test_cell_ids_cover_36_english_combinations() -> None:
    assert len(mm.CELL_IDS) == 36
    assert "hostile_dreadful" in mm.CELL_IDS
    assert "intimate_great" in mm.CELL_IDS
    # 标签全英文 (无中文混入)
    for cid in mm.CELL_IDS:
        assert re.fullmatch(r"[a-z_]+", cid), f"非英文标签: {cid}"


def test_default_cells_all_present() -> None:
    """36 个默认格子文件齐全且非空."""
    for cid in mm.CELL_IDS:
        text = mm.get_cell_text(cid)
        assert text, f"cell {cid} 为空或缺失"


def test_override_cell_takes_priority(_isolation: Path) -> None:
    """覆盖层单文件优先于默认."""
    (_isolation / "friend_good.md").write_text("（自定义覆盖）", encoding="utf-8")
    assert mm.get_cell_text("friend_good") == "（自定义覆盖）"
    assert mm.get_cell_text("friend_dreadful")  # 未覆盖 → 默认


def test_load_mood_matrix_merges_override(_isolation: Path) -> None:
    """load_mood_matrix: defaults 打底 + 覆盖."""
    (_isolation / "friend_good.md").write_text("覆盖文本", encoding="utf-8")
    cells = mm.load_mood_matrix()
    assert len(cells) == 36
    assert cells["friend_good"] == "覆盖文本"
    assert cells["intimate_great"]  # 默认保留


def test_load_override_cells_only_edited(_isolation: Path) -> None:
    (_isolation / "friend_good.md").write_text("A", encoding="utf-8")
    (_isolation / "hostile_dreadful.md").write_text("B", encoding="utf-8")
    assert mm.load_override_cells() == {"friend_good": "A", "hostile_dreadful": "B"}


def test_get_mood_cell_with_cells_dict() -> None:
    cells = {"friend_good": "自定义"}
    assert mm.get_mood_cell("friend", "good", cells) == "自定义"
    assert mm.get_mood_cell("friend", "great", cells) == ""


def test_build_persona_state_section_zh_label_no_values() -> None:
    section = mm.build_persona_state_section(favor_tier="friend", mood_label="good")
    assert "friend" in section
    assert "心情好" in section  # 中文显示映射
    assert "0." not in section  # 不注入连续数值


def test_build_persona_state_section_unknown_falls_back_to_head() -> None:
    section = mm.build_persona_state_section(favor_tier="bogus", mood_label="good")
    assert "[人格状态]" in section


def test_build_state_section_composes_matrix_cause_anchor() -> None:
    section = mm.build_state_section(
        favor_tier="friend",
        mood_state={"tier": "good", "cause": "收到礼物", "valence": 0.4},
        anchor_text="对当前发言者的近期情绪：对方道歉了",
    )
    assert "[人格状态]" in section
    assert "收到礼物" in section
    assert "对当前发言者的近期情绪：对方道歉了" in section

    # mood_state 缺失 → 仅锚点
    section2 = mm.build_state_section(favor_tier="stranger", mood_state=None, anchor_text="锚点文本")
    assert section2 == "锚点文本"
