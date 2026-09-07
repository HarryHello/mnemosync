"""admin_prompt_cleaning 路由回归测试 (T: request 注入修复).

修复前所有端点用 `request: Any` — FastAPI 把 request 当 query 参数,
任何请求都 422 "Field required: request". 修复后应全部 200.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin_prompt_cleaning import router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.persistence.auth_store import User
from src.persistence.prompt_cache_store import PromptCacheStore


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    store = PromptCacheStore(str(tmp_path / "pc.db"))
    await store.connect()
    app = FastAPI()
    app.include_router(router)
    app.state = AppState(prompt_cache_store=store)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="test", username="test", password_hash="",
        must_change_password=False, is_active=True, created_at=None, updated_at=None,
    )
    yield app
    await store.close()


def test_cache_list_does_not_require_request_param(app: FastAPI) -> None:
    """回归: ?page=1&page_size=50 必须 200 (不再 422)."""
    client = TestClient(app)
    resp = client.get("/prompt-cleaning/cache?page=1&page_size=50")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}


def test_settings_list_ok(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/prompt-cleaning/settings")
    assert resp.status_code == 200, resp.text


def test_clear_cache_ok(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.delete("/prompt-cleaning/cache")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 0
