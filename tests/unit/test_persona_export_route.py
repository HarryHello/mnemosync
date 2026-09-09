"""GET /admin/persona/export 导出端点测试 (v0.4.1-beta.1 服务器 500 回归).

覆盖: 正常导出 / 旧版 definition JSON (缺 v0.4.1 字段) 导出 / 无激活人格 404.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
from src.persistence.auth_store import User
from src.persistence.persona_store import SqlitePersonaStore


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    persona_store = SqlitePersonaStore(str(tmp_path / "persona.db"))
    await persona_store.connect()

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)
    app.state = AppState(persona_store=persona_store)

    def _fake_user() -> User:
        return User(
            id="test", username="test", password_hash="",
            must_change_password=False,
            is_active=True, created_at=None, updated_at=None,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    yield app
    await persona_store.close()


def _seed_old_definition(db_path: Path, definition: dict) -> None:
    """绕过 save(), 直接插入旧版本行 (模拟 v0.3.x 升级遗留数据)."""
    import asyncio

    async def _run() -> None:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO personas (id, name, description, is_active, created_at, updated_at) "
                "VALUES ('p1', '默认人格', NULL, 1, datetime('now'), datetime('now'))"
            )
            await db.execute(
                "INSERT INTO persona_versions "
                "(version, name, definition, changelog, author, created_at, active, persona_id) "
                "VALUES (1, '绫音', ?, 'seed', NULL, datetime('now'), 1, 'p1')",
                (json.dumps(definition, ensure_ascii=False),),
            )
            await db.commit()

    asyncio.run(_run())


def test_export_current_definition(app: FastAPI, tmp_path: Path) -> None:
    """新版 save() 后导出 → 200 + JSON 下载头."""
    from src.core.persona.definition import PersonaDefinition, PersonaIdentity

    store: SqlitePersonaStore = app.state.persona_store
    d = PersonaDefinition(version="1", name="绫音", identity=PersonaIdentity())
    asyncio_run = __import__("asyncio").run
    asyncio_run(store.save(d, changelog="t", author="tester"))

    client = TestClient(app)
    resp = client.get("/panel/admin/persona/export")
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    body = resp.json()
    assert body["name"] == "绫音"
    assert body["relationship_alpha"] == "normal"


def test_export_legacy_definition_without_v041_fields(app: FastAPI, tmp_path: Path) -> None:
    """旧版 definition (无 relationship_alpha / identity 子字段缺失) → 不应 500."""
    _seed_old_definition(tmp_path / "persona.db", {
        "version": "5",
        "name": "绫音",
        "identity": {"personality": "内向", "speaking_style": "简短"},
        "space_overrides": {},
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    client = TestClient(app)
    resp = client.get("/panel/admin/persona/export")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "绫音"
    assert body["identity"]["persona_addressing"] == "人格"


def test_export_no_active_persona(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/panel/admin/persona/export")
    assert resp.status_code == 404
