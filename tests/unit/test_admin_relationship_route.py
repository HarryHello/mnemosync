"""GET /panel/admin/relationship 行为测试.

关系尚未建立时应该返回默认 stranger/0/0, 而不是 404 —
新装或人格重置后, 面板加载关系页不应报错.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.core.memory.models import Relationship
from src.persistence.auth_store import User
from src.persistence.memory_store import SqliteMemoryStore


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    memory_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    loop.run_until_complete(memory_store.connect())

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    app.state.memory_store = memory_store
    app.dependency_overrides[get_current_user] = lambda: User(
        id="test", username="test", password_hash="",
        must_change_password=False,
        is_active=True, created_at=None, updated_at=None,
    )

    yield app

    loop.run_until_complete(memory_store.close())
    loop.close()


def test_relationship_missing_returns_default_stranger(app: FastAPI) -> None:
    """新装/重置后 relationships 表空 → 应返回 stranger/0/0, 不是 404."""
    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=test-user")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persona_id"] == "default"
    assert body["user_id"] == "test-user"
    assert body["intimacy"] == 0.0
    assert body["trust"] == 0.0
    assert body["relationship_type"] == "stranger"
    assert body["updated_at"] == ""  # 前端据此显示 "尚未建立" 提示


def test_relationship_returns_stored_row_when_present(app: FastAPI) -> None:
    """存在真实行时应返回该行, 不被默认值覆盖."""
    import asyncio
    loop = asyncio.new_event_loop()
    memory_store: SqliteMemoryStore = app.state.memory_store

    rel = Relationship.create("default", "test-user")
    rel.intimacy_score = 0.42
    rel.trust_level = 0.31
    rel.type = "acquaintance"
    rel.last_active = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc)
    try:
        loop.run_until_complete(memory_store.save_relationship(rel))
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=test-user")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intimacy"] == pytest.approx(0.42)
    assert body["trust"] == pytest.approx(0.31)
    assert body["relationship_type"] == "acquaintance"
    assert body["updated_at"] != ""


def test_relationship_missing_for_unknown_user(app: FastAPI) -> None:
    """未知 user_id 也返回默认 stranger, 与人格首次遇见新用户的语义一致."""
    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=someone-new")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "someone-new"
    assert body["intimacy"] == 0.0
    assert body["trust"] == 0.0
    assert body["relationship_type"] == "stranger"


# ─── v0.2.10 动态称呼演化 ────────────────────────────


def test_response_falls_back_to_toml_addressing_when_null(app: FastAPI) -> None:
    """新装 → 表空 → 响应用 settings.persona.relation.* 填充 addressing."""
    from src.core.config import get_settings
    base = get_settings().persona.relation
    client = TestClient(app)
    resp = client.get("/panel/admin/relationship?user_id=test-user")
    body = resp.json()
    assert body["persona_addressing"] == base.persona_addressing
    assert body["user_addressing"] == base.user_addressing
    assert body["context"] == base.context


def test_put_relationship_updates_addressing_and_writes_audit(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={
            "user_id": "test-user",
            "user_addressing": "小哥",
            "reason": "人工设置初值 (面板)",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_addressing"] == "小哥"

    audit = client.get("/panel/admin/relationship/audit?user_id=test-user").json()
    assert len(audit["items"]) == 1
    assert audit["items"][0]["source"] == "manual"
    assert audit["items"][0]["field_name"] == "user_addressing"
    assert audit["items"][0]["new_value"] == "小哥"


def test_put_relationship_rejects_missing_reason(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={"user_id": "test-user", "user_addressing": "小哥"},
    )
    assert resp.status_code == 422  # Pydantic reason required


def test_put_relationship_rejects_too_short_reason(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={"user_id": "test-user", "user_addressing": "小哥", "reason": "短"},
    )
    assert resp.status_code == 422  # min_length=5


def test_put_relationship_rejects_all_none_fields(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={"user_id": "test-user", "reason": "只有 reason, 没传字段"},
    )
    assert resp.status_code == 400


def test_audit_orders_by_id_desc(app: FastAPI) -> None:
    client = TestClient(app)
    client.put("/panel/admin/relationship",
               json={"user_id": "test-user", "user_addressing": "v1", "reason": "first change"})
    client.put("/panel/admin/relationship",
               json={"user_id": "test-user", "user_addressing": "v2", "reason": "second change"})
    audit = client.get("/panel/admin/relationship/audit?user_id=test-user").json()
    assert len(audit["items"]) == 2
    assert audit["items"][0]["new_value"] == "v2"
    assert audit["items"][1]["new_value"] == "v1"
