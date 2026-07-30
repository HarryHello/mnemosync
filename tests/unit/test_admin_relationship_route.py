"""GET /panel/admin/relationship 行为测试.

关系尚未建立时应该返回默认 stranger/0/0, 而不是 404 —
新装或人格重置后, 面板加载关系页不应报错.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from src.api.routes.admin import router as admin_router
from src.api.routes.auth import get_current_user
from src.api.state import AppState
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

    from src.persistence.identity_store import SqliteIdentityStore
    identity_store = SqliteIdentityStore(str(tmp_path / "identity.db"))
    loop.run_until_complete(identity_store.connect())

    app = FastAPI()
    outer = APIRouter(prefix="/panel")
    outer.include_router(admin_router)
    app.include_router(outer)

    app.state = AppState(memory_store=memory_store, identity_store=identity_store)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="test", username="test", password_hash="",
        must_change_password=False,
        is_active=True, created_at=None, updated_at=None,
    )

    yield app

    loop.run_until_complete(memory_store.close())
    loop.run_until_complete(identity_store.close())
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
    rel.last_active = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
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


# ─── v0.3.0 关系按 Actor 解析 ─────────────────────────


def test_relationship_requires_user_or_actor_id(app: FastAPI) -> None:
    """两个标识都不传 → 400."""
    client = TestClient(app)
    resp = client.get("/panel/admin/relationship")
    assert resp.status_code == 400


def test_relationship_resolves_actor_to_group(app: FastAPI) -> None:
    """绑定 UserGroup 的 Actor → 查到的是组关系 (effective_user_id 收敛)."""
    import asyncio

    from src.persistence.identity_store import SqliteIdentityStore

    loop = asyncio.new_event_loop()
    identity_store: SqliteIdentityStore = app.state.identity_store
    memory_store: SqliteMemoryStore = app.state.memory_store
    try:
        actor_qq = loop.run_until_complete(
            identity_store.find_or_create_actor("12345", "astrbot", "小明")
        )
        actor_dc = loop.run_until_complete(
            identity_store.find_or_create_actor("67890", "maibot", "小明")
        )
        group = loop.run_until_complete(identity_store.create_group("张三"))
        loop.run_until_complete(identity_store.bind_actor_to_group(actor_qq.id, group.id))
        loop.run_until_complete(identity_store.bind_actor_to_group(actor_dc.id, group.id))

        # 在组 (effective_user_id) 上写关系
        rel = Relationship.create("default", group.id)
        rel.intimacy_score = 0.55
        rel.type = "friend"
        loop.run_until_complete(memory_store.save_relationship(rel))
    finally:
        loop.close()

    client = TestClient(app)
    # 两个平台的 Actor 都查到同一份组关系
    for actor_id in (actor_qq.id, actor_dc.id):
        resp = client.get(f"/panel/admin/relationship?actor_id={actor_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == group.id
        assert body["intimacy"] == pytest.approx(0.55)
        assert body["relationship_type"] == "friend"


def test_relationship_actor_without_group_uses_actor_id(app: FastAPI) -> None:
    """未绑组的 Actor → effective_user_id 就是 actor_id 本身."""
    import asyncio

    from src.persistence.identity_store import SqliteIdentityStore

    loop = asyncio.new_event_loop()
    identity_store: SqliteIdentityStore = app.state.identity_store
    try:
        actor = loop.run_until_complete(
            identity_store.find_or_create_actor("99999", "chatbox", "独行侠")
        )
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.get(f"/panel/admin/relationship?actor_id={actor.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == actor.id
    assert body["relationship_type"] == "stranger"  # 尚未建立 → 默认值


def test_put_and_audit_by_actor_id(app: FastAPI) -> None:
    """PUT 支持 actor_id 定位; 审计也能按 actor_id 查."""
    import asyncio

    from src.persistence.identity_store import SqliteIdentityStore

    loop = asyncio.new_event_loop()
    identity_store: SqliteIdentityStore = app.state.identity_store
    try:
        actor = loop.run_until_complete(
            identity_store.find_or_create_actor("54321", "web", "面板用户")
        )
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={"actor_id": actor.id, "user_addressing": "老板", "reason": "按 actor 定位的人工设置"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_addressing"] == "老板"
    assert resp.json()["user_id"] == actor.id

    audit = client.get(f"/panel/admin/relationship/audit?actor_id={actor.id}").json()
    assert len(audit["items"]) == 1
    assert audit["items"][0]["new_value"] == "老板"


def test_put_relationship_requires_user_or_actor_id(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.put(
        "/panel/admin/relationship",
        json={"user_addressing": "小哥", "reason": "没有身份标识的尝试"},
    )
    assert resp.status_code == 400


# ─── v0.3.0 关系列表 ─────────────────────────


def test_list_relationships_empty(app: FastAPI) -> None:
    """空表 → items=[] total=0."""
    client = TestClient(app)
    resp = client.get("/panel/admin/relationships")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 20


def test_list_relationships_returns_all(app: FastAPI) -> None:
    """写入多条 → 全部返回, 默认按亲密度降序."""
    import asyncio
    loop = asyncio.new_event_loop()
    store: SqliteMemoryStore = app.state.memory_store
    try:
        for _i, (uid, intimacy) in enumerate([("alice", 0.42), ("bob", 0.91), ("carol", 0.15)]):
            rel = Relationship.create("default", uid)
            rel.intimacy_score = intimacy
            loop.run_until_complete(store.save_relationship(rel))
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.get("/panel/admin/relationships")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # 默认亲密度降序: bob > alice > carol
    assert body["items"][0]["user_id"] == "bob"
    assert body["items"][1]["user_id"] == "alice"
    assert body["items"][2]["user_id"] == "carol"
    assert all(item["identity"] is None for item in body["items"])


def test_list_relationships_enriches_actor_identity(app: FastAPI) -> None:
    """Actor 关系应包含平台、外部账号和昵称，而不是只暴露内部 ID."""
    import asyncio

    from src.persistence.identity_store import SqliteIdentityStore

    loop = asyncio.new_event_loop()
    identity_store: SqliteIdentityStore = app.state.identity_store
    memory_store: SqliteMemoryStore = app.state.memory_store
    try:
        actor = loop.run_until_complete(
            identity_store.find_or_create_actor("123456", "astrbot", "小明")
        )
        rel = Relationship.create("default", actor.id)
        loop.run_until_complete(memory_store.save_relationship(rel))
    finally:
        loop.close()

    body = TestClient(app).get("/panel/admin/relationships").json()
    identity = body["items"][0]["identity"]
    assert identity["kind"] == "actor"
    assert identity["name"] is None
    assert identity["accounts"] == [{
        "actor_id": actor.id,
        "frontend": "astrbot",
        "external_key": "123456",
        "display_name": "小明",
    }]


def test_relationship_enriches_group_identity(app: FastAPI) -> None:
    """UserGroup 关系应显示组名及其全部跨平台账号."""
    import asyncio

    from src.persistence.identity_store import SqliteIdentityStore

    loop = asyncio.new_event_loop()
    identity_store: SqliteIdentityStore = app.state.identity_store
    memory_store: SqliteMemoryStore = app.state.memory_store
    try:
        qq = loop.run_until_complete(
            identity_store.find_or_create_actor("10001", "astrbot", "小明QQ")
        )
        discord = loop.run_until_complete(
            identity_store.find_or_create_actor("discord-1", "discord", "小明DC")
        )
        group = loop.run_until_complete(identity_store.create_group("张三"))
        loop.run_until_complete(identity_store.bind_actor_to_group(qq.id, group.id))
        loop.run_until_complete(identity_store.bind_actor_to_group(discord.id, group.id))
        rel = Relationship.create("default", group.id)
        loop.run_until_complete(memory_store.save_relationship(rel))
    finally:
        loop.close()

    body = TestClient(app).get(
        f"/panel/admin/relationship?user_id={group.id}"
    ).json()
    assert body["identity"]["kind"] == "group"
    assert body["identity"]["name"] == "张三"
    assert {a["external_key"] for a in body["identity"]["accounts"]} == {
        "10001", "discord-1",
    }


def test_list_relationships_pagination(app: FastAPI) -> None:
    """page_size=2 第一页 2 条, 第二页 1 条."""
    import asyncio
    loop = asyncio.new_event_loop()
    store: SqliteMemoryStore = app.state.memory_store
    try:
        for i in range(5):
            rel = Relationship.create("default", f"user_{i}")
            rel.intimacy_score = round(i * 0.1, 3)
            loop.run_until_complete(store.save_relationship(rel))
    finally:
        loop.close()

    client = TestClient(app)
    # 第一页
    r1 = client.get("/panel/admin/relationships?page=1&page_size=2")
    assert r1.status_code == 200
    b1 = r1.json()
    assert len(b1["items"]) == 2
    assert b1["total"] == 5
    assert b1["page"] == 1
    assert b1["page_size"] == 2

    # 第二页
    r2 = client.get("/panel/admin/relationships?page=2&page_size=2")
    assert r2.status_code == 200
    b2 = r2.json()
    assert len(b2["items"]) == 2
    assert b2["page"] == 2

    # 第三页 (最后一条)
    r3 = client.get("/panel/admin/relationships?page=3&page_size=2")
    assert r3.status_code == 200
    b3 = r3.json()
    assert len(b3["items"]) == 1
    assert b3["page"] == 3


def test_list_relationships_invalid_sort_falls_back(app: FastAPI) -> None:
    """非法 sort_by 退回 intimacy_score 降序."""
    import asyncio
    loop = asyncio.new_event_loop()
    store: SqliteMemoryStore = app.state.memory_store
    try:
        rel = Relationship.create("default", "alice")
        rel.intimacy_score = 0.5
        loop.run_until_complete(store.save_relationship(rel))
        rel2 = Relationship.create("default", "bob")
        rel2.intimacy_score = 0.9
        loop.run_until_complete(store.save_relationship(rel2))
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.get("/panel/admin/relationships?sort_by=nonexistent")
    assert resp.status_code == 200
    body = resp.json()
    # 降序: bob 在前
    assert body["items"][0]["user_id"] == "bob"
    assert body["items"][1]["user_id"] == "alice"


def test_list_relationships_trust_sort(app: FastAPI) -> None:
    """按 trust_level 升序."""
    import asyncio
    loop = asyncio.new_event_loop()
    store: SqliteMemoryStore = app.state.memory_store
    try:
        for uid, trust in [("alice", 0.3), ("bob", 0.9), ("carol", 0.1)]:
            rel = Relationship.create("default", uid)
            rel.trust_level = trust
            loop.run_until_complete(store.save_relationship(rel))
    finally:
        loop.close()

    client = TestClient(app)
    resp = client.get("/panel/admin/relationships?sort_by=trust_level&sort_order=asc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["user_id"] == "carol"
    assert body["items"][1]["user_id"] == "alice"
    assert body["items"][2]["user_id"] == "bob"
