"""GET /admin/upstream/services/{id}/available-models 路由回归测试 (v0.4.1-beta.5).

回归: service_id 路由曾误改 {service_id:path} (贪婪匹配斜杠), 先注册的
GET /services/{id} 把 /available-models 子路径整个吞掉 → 拉取模型 404.
服务商 id 为面板自建、不含斜杠, 必须保持普通 converter.
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
    s = LLMServiceStore(str(tmp_path / "llm_service.db"))
    await s.init_db()
    await s.save_service(
        LLMServiceProvider.create(service_id="BigModel", base_url="https://x", api_key="k")
    )
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


def test_available_models_not_shadowed_by_service_route(app: FastAPI) -> None:
    """子路径 /available-models 不得被 GET /services/{id} 遮蔽为 404.

    无真实上游时端点会 502/504 (上游错误), 但绝不能是「Service not found」404.
    """
    client = TestClient(app)
    resp = client.get("/panel/admin/upstream/services/BigModel/available-models")
    assert resp.status_code != 404, resp.text
    assert "Service not found" not in resp.text


def test_get_service_still_resolves(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/panel/admin/upstream/services/BigModel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "BigModel"
