"""好感度×情绪矩阵单元测试 (v0.4.1, RFC §5)."""

from __future__ import annotations

from src.core.memory.mood_matrix import (
    CELL_IDS,
    _parse_cells,
    build_persona_state_section,
    load_mood_matrix,
)


def test_cell_ids_cover_36_combinations() -> None:
    assert len(CELL_IDS) == 36
    assert "hostile_心情极差" in CELL_IDS
    assert "intimate_心情极好" in CELL_IDS


def test_parse_cells_extracts_headings() -> None:
    text = "## hostile_心情极差\n\n第一段\n第二段\n\n## friend_心情好\n\n友好引导"
    cells = _parse_cells(text)
    assert cells["hostile_心情极差"] == "第一段\n第二段"
    assert cells["friend_心情好"] == "友好引导"


def test_parse_cells_ignores_invalid_ids() -> None:
    text = "## bogus_cell\n\n无效格子\n\n## hostile_心情极差\n\n有效"
    cells = _parse_cells(text)
    assert "bogus_cell" not in cells
    assert "hostile_心情极差" in cells


def test_load_mood_matrix_default_has_all_cells() -> None:
    cells = load_mood_matrix()
    assert len(cells) == 36
    for cid in CELL_IDS:
        assert cells[cid], f"cell {cid} 为空"


def test_build_persona_state_section_no_values_leak() -> None:
    section = build_persona_state_section(favor_tier="friend", mood_label="心情好")
    assert "friend" in section
    assert "心情好" in section
    # 不注入连续数值
    assert "0." not in section.replace("心情极差", "").replace("心情极好", "")


def test_build_persona_state_section_unknown_cell_falls_back_to_head() -> None:
    section = build_persona_state_section(favor_tier="bogus", mood_label="心情好")
    assert "[人格状态]" in section
