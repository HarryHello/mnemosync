"""Admin /model-bindings REST 路由测试 (v0.4.1: model_id 注册表引用语义).

覆盖:
- 鉴权前置: 未登录 → 401
- CRUD: list / add / reorder / delete
- 无效 role → 400; 模型不存在/已禁用 → 400
- 能力字段随注册表 (绑定不再逐条声明)
- PATCH 换模型 (model_id) + resolver.invalidate()
- 每次 mutation 触发 resolver.invalidate()
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
from src.infra.llm_service.models import LLMServiceProvider, ModelRegistryEntry, ModelType
from src.infra.llm_service.store import LLMServiceStore
from src.persistence.auth_store import User


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[LLMServiceStore]:
    db = tmp_path / "llm_service.db"
    s = LLMServiceStore(str(db))

    await s.init_db()
    # 预置两个 service
    for svc_id in ("s1", "s2"):
        svc = LLMServiceProvider.create(
            service_id=svc_id, base_url="https://x", api_key="k",
        )
        await s.save_service(svc)
    # 预置模型注册表
    entries = [
        ModelRegistryEntry.create("s1", "m1", context_length=131072),
        ModelRegistryEntry.create("s1", "m1b"),
        ModelRegistryEntry.create("s1", "e1", embedding_dim=1024),
        ModelRegistryEntry.create("s1", "disabled", enabled=False),
        ModelRegistryEntry.create("s2", "m2"),
        ModelRegistryEntry.create("s2", "m2b"),
        ModelRegistryEntry.create("s2", "e2"),
    ]
    for e in entries:
        await s.save_model_registry(e)
    yield s


@pytest.fixture
def app(store: LLMServiceStore) -> FastAPI:
    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    resolver = RoleResolver(store)

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.state = AppState(llm_service_store=store, resolver=resolver)
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


def test_model_bindings_require_auth(app_unauth: FastAPI) -> None:
    client = TestClient(app_unauth)
    for method, path in [
        ("GET", "/panel/admin/model-bindings"),
        ("POST", "/panel/admin/model-bindings"),
        ("DELETE", "/panel/admin/model-bindings/main/0"),
        ("PATCH", "/panel/admin/model-bindings/main/0"),
        ("PUT", "/panel/admin/model-bindings/main/reorder"),
    ]:
        resp = client.request(method, path, json={} if method != "GET" else None)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


def test_add_list_and_reorder(app: FastAPI) -> None:
    client = TestClient(app)

    # 空
    resp = client.get("/panel/admin/model-bindings?role=main")
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    # add 两条 (model_id 引用)
    for mid in ["s1:m1", "s2:m2"]:
        resp = client.post(
            "/panel/admin/model-bindings",
            json={"role": "main", "model_id": mid},
        )
        assert resp.status_code == 200, resp.text

    resp = client.get("/panel/admin/model-bindings?role=main")
    items = resp.json()["items"]
    assert [(i["priority"], i["service_id"], i["model"], i["model_id"]) for i in items] == [
        (0, "s1", "m1", "s1:m1"),
        (1, "s2", "m2", "s2:m2"),
    ]

    # reorder: 把 s2/m2 提到 0
    resp = client.put(
        "/panel/admin/model-bindings/main/reorder",
        json={"order": [["s2", "m2"], ["s1", "m1"]]},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [(i["priority"], i["service_id"]) for i in items] == [(0, "s2"), (1, "s1")]


def test_add_invalid_role_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "bogus", "model_id": "s1:m1"},
    )
    assert resp.status_code == 400


def test_add_missing_model_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:does-not-exist"},
    )
    assert resp.status_code == 400
    assert "不存在" in resp.json()["detail"]


def test_add_disabled_model_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:disabled"},
    )
    assert resp.status_code == 400
    assert "禁用" in resp.json()["detail"]


def test_delete_missing_returns_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.delete("/panel/admin/model-bindings/main/99")
    assert resp.status_code == 404


async def test_delete_shifts_priorities_and_invalidates_cache(app: FastAPI) -> None:
    client = TestClient(app)
    for mid in ["s1:m1", "s2:m2"]:
        client.post(
            "/panel/admin/model-bindings",
            json={"role": "main", "model_id": mid},
        )

    # 预热 resolver 缓存
    resolver: RoleResolver = app.state.resolver
    top = await resolver.first(ModelType.MAIN)
    assert top.service_id == "s1"
    initial_version = resolver.version

    # 删除 priority 0
    resp = client.delete("/panel/admin/model-bindings/main/0")
    assert resp.status_code == 200

    # 缓存应被 invalidate (version 提升)
    assert resolver.version > initial_version
    top2 = await resolver.first(ModelType.MAIN)
    assert top2.service_id == "s2"
    assert top2.priority == 0


def test_add_with_priority_shifts_existing(app: FastAPI) -> None:
    client = TestClient(app)
    for mid in ["s1:m1", "s2:m2"]:
        client.post(
            "/panel/admin/model-bindings",
            json={"role": "assist", "model_id": mid},
        )
    # 插入到 priority 0 → 原 s1/s2 各后移一位
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "assist", "model_id": "s2:m2b", "priority": 0},
    )
    assert resp.status_code == 200
    items = client.get("/panel/admin/model-bindings?role=assist").json()["items"]
    assert [(i["priority"], i["service_id"], i["model"]) for i in items] == [
        (0, "s2", "m2b"),
        (1, "s1", "m1"),
        (2, "s2", "m2"),
    ]


def test_reorder_mismatch_returns_400(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:m1"},
    )
    resp = client.put(
        "/panel/admin/model-bindings/main/reorder",
        json={"order": [["s2", "does-not-exist"]]},
    )
    assert resp.status_code == 400


def test_add_second_embedding_returns_409(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "embedding", "model_id": "s1:e1"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "embedding", "model_id": "s2:e2"},
    )
    assert resp.status_code == 409
    assert "嵌入模型只允许一条绑定" in resp.json()["detail"]


def test_capabilities_follow_registry(app: FastAPI) -> None:
    """能力字段随模型注册表: 绑定响应携带注册表声明的 context_length / embedding_dim."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:m1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["context_length"] == 131072  # 注册表声明
    assert body["model_id"] == "s1:m1"
    assert body["display_name"] is None

    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "embedding", "model_id": "s1:e1"},
    )
    assert resp.status_code == 200
    assert resp.json()["embedding_dim"] == 1024


def test_probe_dimension_returns_length(app: FastAPI) -> None:
    """probe-dimension 用真实 Forwarder.embed (mock 掉), 校验维度回传."""
    from unittest.mock import AsyncMock, patch

    from src.infra.forwarder.forwarder import Forwarder

    client = TestClient(app)
    with patch.object(
        Forwarder,
        "embed",
        new=AsyncMock(return_value=[[0.1] * 1024]),
    ):
        resp = client.post(
            "/panel/admin/model-bindings/probe-dimension",
            json={"service_id": "s1", "model": "text-embedding-v3", "dimensions": 1024},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dimensions": 1024}


def test_probe_dimension_unknown_service_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings/probe-dimension",
        json={"service_id": "does-not-exist", "model": "x"},
    )
    assert resp.status_code == 404


async def test_patch_swaps_model_and_invalidates_cache(app: FastAPI) -> None:
    """PATCH model_id 换模型; service_id/model/model_id 联动; resolver 缓存 invalidate."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "model_id": "s1:m1"},
    )
    assert resp.status_code == 200

    resolver: RoleResolver = app.state.resolver
    await resolver.first(ModelType.MAIN)
    initial_version = resolver.version

    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={"model_id": "s2:m2b"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "main"
    assert body["priority"] == 0
    assert body["service_id"] == "s2"
    assert body["model"] == "m2b"
    assert body["model_id"] == "s2:m2b"

    assert resolver.version > initial_version


async def test_patch_embedding_swap_allowed(app: FastAPI) -> None:
    """嵌入单绑定可通过 PATCH 换模型 (保持单条不变)."""
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={"role": "embedding", "model_id": "s1:e1"},
    )
    resp = client.patch(
        "/panel/admin/model-bindings/embedding/0",
        json={"model_id": "s2:e2"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == "s2:e2"
    items = client.get("/panel/admin/model-bindings?role=embedding").json()["items"]
    assert len(items) == 1


def test_patch_empty_model_id_400(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={"model_id": ""},
    )
    assert resp.status_code == 400
