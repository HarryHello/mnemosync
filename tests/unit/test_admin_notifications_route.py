"""Admin /notifications REST 路由测试.

覆盖 5 个端点:
  GET  /notifications                    分页 + unread_only
  GET  /notifications/unread-count
  POST /notifications/{id}/read          存在/已读/不存在
  POST /notifications/mark-all-read
  DELETE /notifications/{id}             命中/不存在
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.persistence.auth_store import User
from src.persistence.notification_store import NotificationStore


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    store = NotificationStore(str(tmp_path / "n.db"))
    loop.run_until_complete(store.connect())

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.state = AppState(notification_store=store)
    app.dependency_overrides[get_current_user] = _fake_user

    yield app

    loop.run_until_complete(store.close())
    loop.close()


def _seed(app: FastAPI, count: int = 3) -> list[int]:
    import asyncio

    store: NotificationStore = app.state.notification_store
    loop = asyncio.new_event_loop()
    try:
        ids: list[int] = []
        for i in range(count):
            nid = loop.run_until_complete(
                store.add(
                    level="warning",
                    category="memory_write_failed",
                    title=f"t{i}",
                    message=f"m{i}",
                    meta={"stage": "embed", "i": i},
                )
            )
            ids.append(nid)
        return ids
    finally:
        loop.close()


def test_list_returns_items_and_counts(app: FastAPI) -> None:
    _seed(app, count=3)
    client = TestClient(app)
    resp = client.get("/panel/admin/notifications")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["unread_count"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert len(body["items"]) == 3
    # 降序
    assert body["items"][0]["title"] == "t2"
    assert body["items"][0]["meta"] == {"stage": "embed", "i": 2}
    assert body["items"][0]["read_at"] is None


def test_list_unread_only_filters(app: FastAPI) -> None:
    ids = _seed(app, count=3)
    client = TestClient(app)

    # 先标记第一条已读 (最老)
    assert client.post(f"/panel/admin/notifications/{ids[0]}/read").status_code == 200

    resp = client.get("/panel/admin/notifications", params={"unread_only": True})
    body = resp.json()
    assert body["total"] == 2
    assert body["unread_count"] == 2
    titles = [item["title"] for item in body["items"]]
    assert "t0" not in titles


def test_unread_count_endpoint(app: FastAPI) -> None:
    _seed(app, count=2)
    client = TestClient(app)
    resp = client.get("/panel/admin/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json() == {"unread_count": 2}


def test_mark_read_hit_then_idempotent(app: FastAPI) -> None:
    ids = _seed(app, count=1)
    client = TestClient(app)

    r1 = client.post(f"/panel/admin/notifications/{ids[0]}/read")
    assert r1.status_code == 200
    assert r1.json() == {"marked": 1}

    r2 = client.post(f"/panel/admin/notifications/{ids[0]}/read")
    assert r2.status_code == 200
    assert r2.json() == {"marked": 0}


def test_mark_read_404_for_missing(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/panel/admin/notifications/99999/read")
    assert resp.status_code == 404


def test_mark_all_read(app: FastAPI) -> None:
    _seed(app, count=3)
    client = TestClient(app)
    resp = client.post("/panel/admin/notifications/mark-all-read")
    assert resp.status_code == 200
    assert resp.json() == {"marked": 3}

    # 再次调用应为 0 (幂等)
    resp2 = client.post("/panel/admin/notifications/mark-all-read")
    assert resp2.json() == {"marked": 0}
    assert client.get("/panel/admin/notifications/unread-count").json()["unread_count"] == 0


def test_delete_hit_then_404(app: FastAPI) -> None:
    ids = _seed(app, count=1)
    client = TestClient(app)

    r1 = client.delete(f"/panel/admin/notifications/{ids[0]}")
    assert r1.status_code == 200
    body = r1.json()
    assert body["success"] is True
    assert body["id"] == ids[0]

    # 再删返 404
    r2 = client.delete(f"/panel/admin/notifications/{ids[0]}")
    assert r2.status_code == 404
