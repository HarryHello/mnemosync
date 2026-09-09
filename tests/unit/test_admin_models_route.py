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
        json={
            "service_id": "s1",
            "models": [
                {"model": "exists"},
                {"model": "new-a"},
                {"model": "new-b"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"added": 2, "skipped": 1}

    items = client.get("/panel/admin/models?service_id=s1").json()
    assert {i["model"] for i in items} == {"exists", "new-a", "new-b"}


def test_import_carries_upstream_capabilities(app: FastAPI) -> None:
    """导入携带上游声明的能力: context_length / output_limit / modalities."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models:import",
        json={
            "service_id": "s1",
            "models": [
                {
                    "model": "vision-model",
                    "context_length": 131072,
                    "output_limit": 8192,
                    "input_modalities": ["text", "image"],
                },
                {"model": "plain", "context_length": 4096},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"added": 2, "skipped": 0}

    items = {i["model"]: i for i in client.get("/panel/admin/models?service_id=s1").json()}
    assert items["vision-model"]["context_length"] == 131072
    assert items["vision-model"]["output_limit"] == 8192
    assert items["vision-model"]["input_modalities"] == ["text", "image"]
    assert items["vision-model"]["display_name"] == "s1/vision-model"
    assert items["plain"]["context_length"] == 4096
    assert items["plain"]["output_limit"] is None




def test_supports_tools_crud(app: FastAPI) -> None:
    """工具调用能力: create / patch / import 全程贯通."""
    client = TestClient(app)

    resp = client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "tool-m", "supports_tools": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["supports_tools"] is True

    # patch 关闭
    resp = client.patch("/panel/admin/models/s1:tool-m", json={"supports_tools": False})
    assert resp.status_code == 200
    assert resp.json()["supports_tools"] is False

    # import 带工具能力
    resp = client.post(
        "/panel/admin/models:import",
        json={
            "service_id": "s1",
            "models": [{"model": "tool-import", "supports_tools": True}],
        },
    )
    assert resp.status_code == 200
    items = {i["model"]: i for i in client.get("/panel/admin/models?service_id=s1").json()}
    assert items["tool-import"]["supports_tools"] is True

def test_import_unknown_service_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models:import",
        json={"service_id": "nope", "models": [{"model": "a"}]},
    )
    assert resp.status_code == 400


def test_display_name_falls_back_and_concurrency_default(app: FastAPI) -> None:
    """单个创建: 默认 concurrency 20, 显示名空 (由前端填默认)."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models", json={"service_id": "s1", "model": "plain"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["concurrency"] == 20
    assert body["display_name"] is None


def test_create_with_output_limit(app: FastAPI) -> None:
    """输出上限字段随创建/响应透传."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "out-m", "context_length": 131072, "output_limit": 8192},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["context_length"] == 131072
    assert body["output_limit"] == 8192


def test_patch_output_limit_and_clear(app: FastAPI) -> None:
    client = TestClient(app)
    client.post("/panel/admin/models", json={"service_id": "s1", "model": "m1"})
    resp = client.patch("/panel/admin/models/s1:m1", json={"output_limit": 16384})
    assert resp.status_code == 200
    assert resp.json()["output_limit"] == 16384

    resp = client.patch("/panel/admin/models/s1:m1", json={"output_limit": None})
    assert resp.status_code == 200
    assert resp.json()["output_limit"] is None


def test_import_default_display_name(app: FastAPI) -> None:
    """批量导入的显示名默认为 服务商id/model_name."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models:import",
        json={"service_id": "s1", "models": [{"model": "deepseek-chat"}, {"model": "deepseek-r1"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"added": 2, "skipped": 0}

    items = client.get("/panel/admin/models?service_id=s1").json()
    by_model = {i["model"]: i for i in items}
    assert by_model["deepseek-chat"]["display_name"] == "s1/deepseek-chat"
    assert by_model["deepseek-chat"]["concurrency"] == 20
    assert by_model["deepseek-chat"]["output_limit"] is None



def test_slash_in_model_id_crud(app: FastAPI) -> None:
    """回归 (v0.4.1-beta.1): 模型名含 '/' (OpenRouter 形态) 时 CRUD 曾 404.

    路由参数默认 converter 不匹配斜杠, 列表能显示但 PATCH/DELETE 寻址失败.
    """
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "deepseek/deepseek-v4-flash"},
    )
    assert resp.status_code == 200, resp.text
    mid = resp.json()["id"]
    assert mid == "s1:deepseek/deepseek-v4-flash"

    resp = client.patch(
        f"/panel/admin/models/{mid}", json={"display_name": "DS V4 Flash"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "DS V4 Flash"

    resp = client.delete(f"/panel/admin/models/{mid}")
    assert resp.status_code == 200, resp.text
    assert client.get("/panel/admin/models?service_id=s1").json() == []


def test_model_kind_crud(app: FastAPI) -> None:
    """v0.4.1: model_kind (chat/embedding/rerank) 创建与更新."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/models",
        json={"service_id": "s1", "model": "text-embedding-v3",
              "model_kind": "embedding", "embedding_dim": 1024},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_kind"] == "embedding"

    resp = client.patch(
        "/panel/admin/models/s1:text-embedding-v3", json={"model_kind": "rerank"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_kind"] == "rerank"

    # 非法值 400
    resp = client.patch(
        "/panel/admin/models/s1:text-embedding-v3", json={"model_kind": "bogus"}
    )
    assert resp.status_code == 400

    # 默认 chat
    resp = client.post("/panel/admin/models", json={"service_id": "s1", "model": "m2"})
    assert resp.json()["model_kind"] == "chat"
