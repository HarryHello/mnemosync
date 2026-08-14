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


def test_override_merge_only_replaces_edited_cells(tmp_path, monkeypatch) -> None:
    """覆盖文件只写 1 格 → 其余 35 格保留 defaults 内容 (覆盖合并, T1)."""
    from pathlib import Path

    from src.core.agents import prompts as prompts_pkg
    from src.core.memory import mood_matrix as mm
    from src.core.prompts import PromptStore

    default_dir = Path(prompts_pkg.__file__).parent / "defaults"
    override_dir = tmp_path / "overrides"
    override_dir.mkdir()
    # 覆盖文件只写一个格子
    (override_dir / "mood_matrix.md").write_text(
        "## hostile_心情极差\n\n（自定义覆盖文本：你对他已经忍无可忍。）\n",
        encoding="utf-8",
    )
    store = PromptStore(override_dir=override_dir, default_dir=default_dir)
    monkeypatch.setattr(mm, "get_prompt_store", lambda: store)

    cells = mm.load_mood_matrix()
    assert len(cells) == 36
    assert cells["hostile_心情极差"] == "（自定义覆盖文本：你对他已经忍无可忍。）"
    # 未覆盖的格子保留默认内容
    assert "intimate_心情极好" in cells and cells["intimate_心情极好"]
    assert cells["intimate_心情极好"] != "（自定义覆盖文本：你对他已经忍无可忍。）"


def test_build_state_section_composes_matrix_cause_anchor() -> None:
    """共享 build_state_section: 矩阵格 + cause + 锚点 (T4)."""
    from src.core.memory.mood_matrix import build_state_section

    section = build_state_section(
        favor_tier="friend",
        mood_state={"tier": "心情好", "cause": "收到礼物", "valence": 0.4},
        anchor_text="对当前发言者的近期情绪：对方道歉了",
    )
    assert "[人格状态]" in section
    assert "收到礼物" in section
    assert "对当前发言者的近期情绪：对方道歉了" in section

    # mood_state 缺失 → 仅锚点
    section2 = build_state_section(favor_tier="stranger", mood_state=None, anchor_text="锚点文本")
    assert section2 == "锚点文本"

