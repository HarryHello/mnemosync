"""mood_matrix 格子编辑测试: save_cell/reset_cell/路由 (每格一文件, 英文标签)."""

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
    """覆盖层指向临时目录, 避免污染 data/prompts."""
    od = tmp_path / "prompts" / "mood_matrix"
    od.mkdir(parents=True)
    monkeypatch.setattr(mm, "_override_cell_path", lambda cid: od / f"{cid}.md")
    return od


# ─── save_cell / reset_cell ─────────────────────────────────

def test_save_cell_writes_only_that_file(_override_dir: Path) -> None:
    mm.save_cell("friend_good", "（自定义：心情好时对朋友格外热情）")
    # 覆盖层只有这一格 (单文件)
    cells = mm.load_override_cells()
    assert cells == {"friend_good": "（自定义：心情好时对朋友格外热情）"}
    # 合并加载: 36 格齐全且该格被覆盖
    merged = mm.load_mood_matrix()
    assert len(merged) == 36
    assert merged["friend_good"] == "（自定义：心情好时对朋友格外热情）"
    assert merged["intimate_great"]  # 默认保留


def test_save_cell_keeps_other_overrides(_override_dir: Path) -> None:
    mm.save_cell("friend_good", "A")
    mm.save_cell("hostile_dreadful", "B")
    assert mm.load_override_cells() == {"friend_good": "A", "hostile_dreadful": "B"}


def test_save_cell_overwrites_existing(_override_dir: Path) -> None:
    mm.save_cell("friend_good", "旧")
    mm.save_cell("friend_good", "新")
    assert mm.load_override_cells() == {"friend_good": "新"}


def test_save_cell_empty_text_resets(_override_dir: Path) -> None:
    mm.save_cell("friend_good", "内容")
    mm.save_cell("friend_good", "")
    assert mm.load_override_cells() == {}
    assert not (_override_dir / "friend_good.md").exists()


def test_save_cell_invalid_id_rejected(_override_dir: Path) -> None:
    with pytest.raises(ValueError):
        mm.save_cell("bogus_格子", "x")
    with pytest.raises(ValueError):
        mm.reset_cell("bogus_格子")


def test_reset_cell_removes_only_that_cell(_override_dir: Path) -> None:
    mm.save_cell("friend_good", "A")
    mm.save_cell("hostile_dreadful", "B")
    assert mm.reset_cell("friend_good") is True
    assert mm.load_override_cells() == {"hostile_dreadful": "B"}
    assert mm.reset_cell("friend_good") is False


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
    # 档位/心情段: id 英文 + label 中文
    labels = {t["id"]: t["label"] for t in body["favor_tiers"]}
    assert labels["hostile"] == "敌对"
    moods = {m["id"]: m["label"] for m in body["mood_labels"]}
    assert moods["dreadful"] == "心情极差"
    assert moods["great"] == "心情极好"


def test_put_cell_then_get_shows_overridden(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put("/mood-matrix/friend_good", json={"text": "测试文本"})
    assert resp.status_code == 200, resp.text

    body = client.get("/mood-matrix").json()
    cell = next(c for c in body["cells"] if c["id"] == "friend_good")
    assert cell["text"] == "测试文本"
    assert cell["overridden"] is True


def test_delete_cell_resets(app: FastAPI) -> None:
    client = TestClient(app)
    client.put("/mood-matrix/friend_good", json={"text": "测试文本"})
    resp = client.delete("/mood-matrix/friend_good")
    assert resp.status_code == 200
    body = client.get("/mood-matrix").json()
    cell = next(c for c in body["cells"] if c["id"] == "friend_good")
    assert cell["overridden"] is False
    assert cell["text"]  # 回默认文本


def test_put_invalid_cell_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put("/mood-matrix/bogus_格子", json={"text": "x"})
    assert resp.status_code == 400
