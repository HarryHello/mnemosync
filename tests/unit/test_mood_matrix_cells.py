"""mood_matrix 格子编辑测试: save_cell/reset_cell/路由 (面板 6×6 grid)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin_mood_matrix import router as mood_matrix_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.core.memory import mood_matrix as mm
from src.persistence.auth_store import User


@pytest.fixture(autouse=True)
def _override_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """覆盖层指向临时目录 + PromptStore 指向 (tmp override, 真实 defaults)."""
    from src.core.agents import prompts as prompts_pkg
    from src.core.prompts import PromptStore

    od = tmp_path / "prompts"
    od.mkdir()
    default_dir = Path(prompts_pkg.__file__).parent / "defaults"
    store = PromptStore(override_dir=od, default_dir=default_dir)
    monkeypatch.setattr(mm, "get_prompt_store", lambda: store)
    monkeypatch.setattr(mm, "_override_path", lambda: od / "mood_matrix.md")
    return od


# ─── save_cell / reset_cell ─────────────────────────────────

def test_save_cell_writes_only_that_cell(tmp_path: Path) -> None:
    mm.save_cell("friend_心情好", "（自定义：心情好时对朋友格外热情）")
    # 覆盖层只有这一格
    cells = mm.load_override_cells()
    assert cells == {"friend_心情好": "（自定义：心情好时对朋友格外热情）"}
    # 合并加载: 36 格齐全且该格被覆盖
    merged = mm.load_mood_matrix()
    assert len(merged) == 36
    assert merged["friend_心情好"] == "（自定义：心情好时对朋友格外热情）"
    assert merged["intimate_心情极好"]  # 默认保留


def test_save_cell_keeps_other_overrides(tmp_path: Path) -> None:
    mm.save_cell("friend_心情好", "A")
    mm.save_cell("hostile_心情极差", "B")
    cells = mm.load_override_cells()
    assert cells == {"friend_心情好": "A", "hostile_心情极差": "B"}


def test_save_cell_overwrites_existing(tmp_path: Path) -> None:
    mm.save_cell("friend_心情好", "旧")
    mm.save_cell("friend_心情好", "新")
    assert mm.load_override_cells() == {"friend_心情好": "新"}


def test_save_cell_empty_text_resets(tmp_path: Path) -> None:
    mm.save_cell("friend_心情好", "内容")
    mm.save_cell("friend_心情好", "")
    assert mm.load_override_cells() == {}
    assert not (tmp_path / "prompts" / "mood_matrix.md").exists()


def test_save_cell_invalid_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        mm.save_cell("bogus_格子", "x")
    with pytest.raises(ValueError):
        mm.reset_cell("bogus_格子")


def test_reset_cell_removes_only_that_cell(tmp_path: Path) -> None:
    mm.save_cell("friend_心情好", "A")
    mm.save_cell("hostile_心情极差", "B")
    assert mm.reset_cell("friend_心情好") is True
    assert mm.load_override_cells() == {"hostile_心情极差": "B"}
    # 重复重置 → False
    assert mm.reset_cell("friend_心情好") is False


# ─── 路由 ────────────────────────────────────────────────────

@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    fapp = FastAPI()
    fapp.include_router(mood_matrix_router)
    fapp.state = AppState()
    fapp.dependency_overrides[get_current_user] = lambda: User(
        id="test", username="test", password_hash="",
        must_change_password=False, is_active=True, created_at=None, updated_at=None,
    )
    yield fapp


def test_get_matrix_returns_36_cells(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/mood-matrix")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["favor_tiers"]) == 6
    assert len(body["mood_labels"]) == 6
    assert len(body["cells"]) == 36
    # 档位中文标签
    labels = {t["id"]: t["label"] for t in body["favor_tiers"]}
    assert labels["hostile"] == "敌对"
    assert labels["intimate"] == "亲密"


def test_put_cell_then_get_shows_overridden(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put("/mood-matrix/friend_心情好", json={"text": "测试文本"})
    assert resp.status_code == 200, resp.text

    body = client.get("/mood-matrix").json()
    cell = next(c for c in body["cells"] if c["id"] == "friend_心情好")
    assert cell["text"] == "测试文本"
    assert cell["overridden"] is True


def test_delete_cell_resets(app: FastAPI) -> None:
    client = TestClient(app)
    client.put("/mood-matrix/friend_心情好", json={"text": "测试文本"})
    resp = client.delete("/mood-matrix/friend_心情好")
    assert resp.status_code == 200
    body = client.get("/mood-matrix").json()
    cell = next(c for c in body["cells"] if c["id"] == "friend_心情好")
    assert cell["overridden"] is False
    assert cell["text"]  # 回默认文本


def test_put_invalid_cell_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put("/mood-matrix/bogus_格子", json={"text": "x"})
    assert resp.status_code == 400
