"""Admin /model-bindings REST 路由测试.

覆盖:
- 鉴权前置: 未登录 → 401
- CRUD: list / add / reorder / delete
- 无效 role → 400
- 缺失 binding → 404
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
from src.infra.llm_service.models import LLMServiceProvider, ModelType
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

    # add 两条
    for sid, model in [("s1", "m1"), ("s2", "m2")]:
        resp = client.post(
            "/panel/admin/model-bindings",
            json={"role": "main", "service_id": sid, "model": model},
        )
        assert resp.status_code == 200, resp.text

    resp = client.get("/panel/admin/model-bindings?role=main")
    items = resp.json()["items"]
    assert [(i["priority"], i["service_id"], i["model"]) for i in items] == [
        (0, "s1", "m1"),
        (1, "s2", "m2"),
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
        json={"role": "bogus", "service_id": "s1", "model": "m"},
    )
    assert resp.status_code == 400


def test_delete_missing_returns_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.delete("/panel/admin/model-bindings/main/99")
    assert resp.status_code == 404


async def test_delete_shifts_priorities_and_invalidates_cache(app: FastAPI) -> None:
    client = TestClient(app)
    for sid, model in [("s1", "m1"), ("s2", "m2")]:
        client.post(
            "/panel/admin/model-bindings",
            json={"role": "main", "service_id": sid, "model": model},
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
    for sid, model in [("s1", "m1"), ("s2", "m2")]:
        client.post(
            "/panel/admin/model-bindings",
            json={"role": "assist", "service_id": sid, "model": model},
        )
    # 插入到 priority 0 → 原 s1/s2 各后移一位
    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "assist", "service_id": "s2", "model": "m2b", "priority": 0},
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
        json={"role": "main", "service_id": "s1", "model": "m1"},
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
        json={"role": "embedding", "service_id": "s1", "model": "e1"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/panel/admin/model-bindings",
        json={"role": "embedding", "service_id": "s2", "model": "e2"},
    )
    assert resp.status_code == 409
    assert "嵌入模型只允许一条绑定" in resp.json()["detail"]


def test_add_with_metadata_fields(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={
            "role": "main",
            "service_id": "s1",
            "model": "m1",
            "context_length": 131072,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["context_length"] == 131072
    assert body["embedding_dim"] is None

    resp = client.post(
        "/panel/admin/model-bindings",
        json={
            "role": "embedding",
            "service_id": "s1",
            "model": "e1",
            "embedding_dim": 1024,
        },
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


async def test_patch_updates_editable_fields_and_invalidates_cache(app: FastAPI) -> None:
    """PATCH 更新 service_id / model / metadata; resolver 缓存应被 invalidate."""
    client = TestClient(app)
    resp = client.post(
        "/panel/admin/model-bindings",
        json={
            "role": "main",
            "service_id": "s1",
            "model": "m1",
            "context_length": 8192,
        },
    )
    assert resp.status_code == 200

    resolver: RoleResolver = app.state.resolver
    await resolver.first(ModelType.MAIN)
    initial_version = resolver.version

    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={
            "service_id": "s2",
            "model": "m1b",
            "context_length": 32768,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "main"
    assert body["priority"] == 0
    assert body["service_id"] == "s2"
    assert body["model"] == "m1b"
    assert body["context_length"] == 32768

    assert resolver.version > initial_version


def test_patch_clears_nullable_fields(app: FastAPI) -> None:
    """显式传 null 应清空 context_length / embedding_dim."""
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={
            "role": "embedding",
            "service_id": "s1",
            "model": "e1",
            "embedding_dim": 1024,
        },
    )
    resp = client.patch(
        "/panel/admin/model-bindings/embedding/0",
        json={"embedding_dim": None},
    )
    assert resp.status_code == 200
    assert resp.json()["embedding_dim"] is None


def test_patch_missing_binding_returns_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.patch(
        "/panel/admin/model-bindings/main/9",
        json={"model": "x"},
    )
    assert resp.status_code == 404


def test_patch_unknown_service_returns_400(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "service_id": "s1", "model": "m1"},
    )
    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={"service_id": "does-not-exist"},
    )
    assert resp.status_code == 400


def test_patch_empty_body_returns_400(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "service_id": "s1", "model": "m1"},
    )
    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={},
    )
    assert resp.status_code == 400


def test_patch_blank_model_returns_400(app: FastAPI) -> None:
    client = TestClient(app)
    client.post(
        "/panel/admin/model-bindings",
        json={"role": "main", "service_id": "s1", "model": "m1"},
    )
    resp = client.patch(
        "/panel/admin/model-bindings/main/0",
        json={"model": "   "},
    )
    assert resp.status_code == 400
