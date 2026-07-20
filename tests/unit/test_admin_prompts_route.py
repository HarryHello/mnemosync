"""Admin prompts REST 路由测试.

覆盖:
- 未登录 → 401 (鉴权前置)
- 已登录: list / get / put (含校验失败) / delete / validate / history
- 路径穿越: 未知 name → 404
- 全部现有 admin 路由 (health/logs/memories/relationship) 未登录也应 401 (安全前置)
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.core.prompts import _reset_prompt_store
from src.core.prompts.registry import PROMPT_REGISTRY
from src.core.prompts.store import PromptStore
from src.persistence.auth_store import User


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def temp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PromptStore]:
    """在临时目录搭 default + override, 并替换全局单例."""
    default_dir = tmp_path / "defaults"
    override_dir = tmp_path / "overrides"
    default_dir.mkdir()
    override_dir.mkdir()
    for name, spec in PROMPT_REGISTRY.items():
        body = f"# default {name}\n"
        for ph in spec.placeholders:
            body += f"__{ph}__\n"
        (default_dir / f"{name}.md").write_text(body, encoding="utf-8")

    store = PromptStore(override_dir=override_dir, default_dir=default_dir)

    from src.core.prompts import store as store_module

    monkeypatch.setattr(store_module, "_store", store)
    yield store
    _reset_prompt_store()


@pytest.fixture
def app_unauth() -> FastAPI:
    """不注入 get_current_user override → 触发 401."""
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    return app


@pytest.fixture
def app_auth() -> FastAPI:
    """override get_current_user 返回一个假 User."""
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    def _fake_user() -> User:
        return User(
            id="test",
            username="test",
            password_hash="",
            must_change_password=False,
            is_active=True,
            created_at=None,
            updated_at=None,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    return app


# ─── 鉴权前置 ──────────────────────────────────────────


def test_all_admin_routes_require_auth(app_unauth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_unauth)
    endpoints = [
        ("GET", "/panel/admin/health"),
        ("GET", "/panel/admin/logs"),
        ("GET", "/panel/admin/memories"),
        ("GET", "/panel/admin/relationship"),
        ("GET", "/panel/admin/prompts"),
        ("GET", "/panel/admin/prompts/memory_analysis"),
    ]
    for method, path in endpoints:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} 未拒绝: {resp.status_code}"


# ─── Prompt 路由 ────────────────────────────────────────


def test_list_prompts_returns_registry(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    resp = client.get("/panel/admin/prompts")
    assert resp.status_code == 200
    body = resp.json()
    assert {item["name"] for item in body} == set(PROMPT_REGISTRY)


def test_get_prompt_detail(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    resp = client.get("/panel/admin/prompts/memory_analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "memory_analysis"
    assert "__SOURCE_USER__" in body["default"]
    # 无覆盖时 current == default (但 default 会带 frontmatter 若有, 这里无)
    assert body["overridden"] is False


def test_get_prompt_unknown_returns_404(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    resp = client.get("/panel/admin/prompts/../etc/passwd")
    # FastAPI 会先在路由匹配时 normalize, 或 registry 检查 → 404
    assert resp.status_code == 404


def test_put_prompt_saves_override(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    content = (
        "__SOURCE_USER__ __CONVERSATION__ __DECAY_TARGETS__ "
        "__PERSONA_NAME__ __PERSONA_ADDRESSING__ __USER_ADDRESSING__ __RELATION_CONTEXT__ custom"
    )
    resp = client.put(
        "/panel/admin/prompts/memory_analysis",
        json={"content": content},
    )
    assert resp.status_code == 200
    assert resp.json()["overridden"] is True
    assert temp_store.load("memory_analysis") == content


def test_put_prompt_missing_placeholder_returns_400(
    app_auth: FastAPI, temp_store: PromptStore
) -> None:
    client = TestClient(app_auth)
    resp = client.put(
        "/panel/admin/prompts/memory_analysis",
        json={"content": "只有 __SOURCE_USER__"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "DECAY_TARGETS" in detail["missing_placeholders"]


def test_delete_prompt_resets_override(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    content = (
        "__SOURCE_USER__ __CONVERSATION__ __DECAY_TARGETS__ "
        "__PERSONA_NAME__ __PERSONA_ADDRESSING__ __USER_ADDRESSING__ __RELATION_CONTEXT__"
    )
    client.put("/panel/admin/prompts/memory_analysis", json={"content": content})

    resp = client.delete("/panel/admin/prompts/memory_analysis")
    assert resp.status_code == 200
    assert resp.json()["overridden"] is False


def test_validate_dry_run_does_not_persist(
    app_auth: FastAPI, temp_store: PromptStore
) -> None:
    client = TestClient(app_auth)
    resp = client.post(
        "/panel/admin/prompts/memory_analysis:validate",
        json={"content": (
            "__SOURCE_USER__ __CONVERSATION__ __DECAY_TARGETS__ "
            "__PERSONA_NAME__ __PERSONA_ADDRESSING__ __USER_ADDRESSING__ __RELATION_CONTEXT__"
        )},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # 未写盘
    assert not (temp_store.override_dir / "memory_analysis.md").exists()


def test_validate_reports_missing(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    resp = client.post(
        "/panel/admin/prompts/memory_analysis:validate",
        json={"content": "缺占位符"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert set(body["missing_placeholders"]) == {
        "SOURCE_USER", "CONVERSATION", "DECAY_TARGETS",
        "PERSONA_NAME", "PERSONA_ADDRESSING", "USER_ADDRESSING", "RELATION_CONTEXT",
    }


def test_history_lists_backups(app_auth: FastAPI, temp_store: PromptStore) -> None:
    client = TestClient(app_auth)
    content = (
        "__SOURCE_USER__ __CONVERSATION__ __DECAY_TARGETS__ "
        "__PERSONA_NAME__ __PERSONA_ADDRESSING__ __USER_ADDRESSING__ __RELATION_CONTEXT__"
    )
    # 需要至少 2 次 save 才有 1 份备份 (首次无旧覆盖)
    client.put("/panel/admin/prompts/memory_analysis", json={"content": content + " v0"})
    client.put("/panel/admin/prompts/memory_analysis", json={"content": content + " v1"})

    resp = client.get("/panel/admin/prompts/memory_analysis/history")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["filename"].startswith("memory_analysis-")
