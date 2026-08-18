"""Admin /models 模型注册表 REST 路由测试 (v0.4.1).

覆盖: CRUD / 重复 409 / 删除被引用 409 / 批量导入 / 鉴权.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.core.models.resolver import RoleResolver
from src.infra.llm_service.models import LLMServiceProvider
from src.infra.llm_service.store import LLMServiceStore
from src.persistence.auth_store import User


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[LLMServiceStore]:
    db = tmp_path / "llm_service.db"
    s = LLMServiceStore(str(db))
    await s.init_db()
    svc = LLMServiceProvider.create(service_id="s1", base_url="https://x", api_key="k")
    await s.save_service(svc)
    yield s


@pytest.fixture
def app(store: LLMServiceStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    app.state = AppState(llm_service_store=store, resolver=RoleResolver(store))

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False, is_active=True,
            created_at=None, updated_at=None,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    return app


@pytest.fixture
def app_unauth(store: LLMServiceStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    app.state = AppState(llm_service_store=store, resolver=RoleResolver(store))
    return app


def test_models_require_auth(app_unauth: FastAPI) -> None:
    client = TestClient(app_unauth)
    for method, path in [
        ("GET", "/panel/admin/models"),
        ("POST", "/panel/admin/models"),
        ("PATCH", "/panel/admin/models/s1:m1"),
        ("DELETE", "/panel/admin/models/s1:m1"),
        ("POST", "/panel/admin/models:import"),
    ]:
        resp = client.request(method, path, json={} if method != "GET" else None)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


def test_create_and_list(app: FastAPI) -> None:
    client = TestClient(app)

    resp = client.post(
        "/panel/admin/models",
        json={
            "service_id": "s1",
            "model": "deepseek-chat",
            "display_name": "DeepSeek Chat",
            "context_length": 131072,
            "concurrency": 30,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "s1:deepseek-chat"
    assert body["display_name"] == "DeepSeek Chat"
    assert body["concurrency"] == 30
    assert body["enabled"] is True

    resp = client.get("/panel/admin/models?service_id=s1")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == "s1:deepseek-chat"

    resp = client.get("/panel/admin/models")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_create_duplicate_409(app: FastAPI) -> None:
    client = TestClient(app)
    payload = {"service_id": "s1", "model": "m1"}
    assert client.post("/panel/admin/models", json=payload).status_code == 200
    resp = client.post("/panel/admin/models", json=payload)
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]


def test_create_unknown_service_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models", json={"service_id": "nope", "model": "m1"}
    )
    assert resp.status_code == 400


def test_patch_updates_fields(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "m1", "concurrency": 20},
    )

    resp = client.patch(
        "/panel/admin/models/s1:m1",
        json={
            "display_name": "M1 模型",
            "concurrency": 0,        # 0 = 不限
            "enabled": False,
            "input_modalities": ["text", "image"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "M1 模型"
    assert body["concurrency"] == 0
    assert body["enabled"] is False
    assert body["input_modalities"] == ["text", "image"]


def test_patch_clear_display_name(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "m1", "display_name": "X"},
    )
    resp = client.patch("/panel/admin/models/s1:m1", json={"display_name": ""})
    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


def test_patch_missing_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.patch("/panel/admin/models/s1:ghost", json={"enabled": True})
    assert resp.status_code == 404


def test_delete_ok_then_referenced_409(app: FastAPI) -> None:
    client = TestClient(app)
    client.post("/panel/admin/models", json={"service_id": "s1", "model": "m1"})
    client.post("/panel/admin/models", json={"service_id": "s1", "model": "m2"})
    # 绑定 m1
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:m1"},
    )
    assert resp.status_code == 200, resp.text

    # 被引用 → 409
    resp = client.delete("/panel/admin/models/s1:m1")
    assert resp.status_code == 409
    assert "解绑" in resp.json()["detail"]

    # 未引用 → 200
    resp = client.delete("/panel/admin/models/s1:m2")
    assert resp.status_code == 200

    # 重复删 → 404
    resp = client.delete("/panel/admin/models/s1:m2")
    assert resp.status_code == 404


def test_import_models_added_and_skipped(app: FastAPI) -> None:
    client = TestClient(app)
    client.post("/panel/admin/models", json={"service_id": "s1", "model": "exists"})

    resp = client.post(
        "/panel/admin/models:import",
        json={"service_id": "s1", "models": ["exists", "new-a", "new-b", "  ", ""]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"added": 2, "skipped": 1}

    items = client.get("/panel/admin/models?service_id=s1").json()
    assert {i["model"] for i in items} == {"exists", "new-a", "new-b"}


def test_import_unknown_service_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models:import",
        json={"service_id": "nope", "models": ["a"]},
    )
    assert resp.status_code == 400


def test_display_name_falls_back_and_concurrency_default(app: FastAPI) -> None:
    """默认 concurrency 20, 显示名空."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models", json={"service_id": "s1", "model": "plain"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["concurrency"] == 20
    assert body["display_name"] is None
