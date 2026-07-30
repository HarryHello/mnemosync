"""LLMServiceStore role_bindings + RoleResolver 单元测试.

覆盖:
- add/list/delete/reorder role_bindings, 优先级让位与前移
- 服务级联删除会顺带清空绑定
- RoleResolver 缓存 + invalidate 版本号
- 空绑定时 for_role(require=True) 抛 NoCandidateForRoleError
"""

from __future__ import annotations

import pytest
from src.core.models.resolver import NoCandidateForRoleError, RoleResolver
from src.infra.llm_service.models import LLMServiceProvider, ModelType
from src.infra.llm_service.store import LLMServiceStore


@pytest.fixture
async def store(tmp_path):
    s = LLMServiceStore(str(tmp_path / "llm.db"))
    await s.init_db()
    # 两个服务, 供绑定使用
    await s.save_service(LLMServiceProvider.create("svc-a", "https://a.example/v1", "sk-aaa"))
    await s.save_service(LLMServiceProvider.create("svc-b", "https://b.example/v1", "sk-bbb"))
    return s


# ─── 基本 CRUD ────────────────────────────────────────────

async def test_add_binding_defaults_to_next_priority(store):
    b1 = await store.add_role_binding(ModelType.MAIN, "svc-a", "qwen-max")
    b2 = await store.add_role_binding(ModelType.MAIN, "svc-b", "claude-3.5")
    assert b1.priority == 0
    assert b2.priority == 1
    listed = await store.list_role_bindings(ModelType.MAIN)
    assert [b.priority for b in listed] == [0, 1]
    assert [b.service_id for b in listed] == ["svc-a", "svc-b"]


async def test_add_binding_with_explicit_priority_makes_room(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "qwen-max")
    await store.add_role_binding(ModelType.MAIN, "svc-b", "claude-3.5")
    # 插到最前面, 现有两条 priority += 1
    inserted = await store.add_role_binding(
        ModelType.MAIN, "svc-a", "qwen-plus", priority=0
    )
    assert inserted.priority == 0
    listed = await store.list_role_bindings(ModelType.MAIN)
    assert [b.model for b in listed] == ["qwen-plus", "qwen-max", "claude-3.5"]
    assert [b.priority for b in listed] == [0, 1, 2]


async def test_add_binding_rejects_unknown_service(store):
    with pytest.raises(ValueError, match="不存在"):
        await store.add_role_binding(ModelType.MAIN, "svc-ghost", "x")


async def test_delete_binding_compacts_priorities(store):
    await store.add_role_binding(ModelType.ASSIST, "svc-a", "a")
    await store.add_role_binding(ModelType.ASSIST, "svc-b", "b")
    await store.add_role_binding(ModelType.ASSIST, "svc-a", "c")
    ok = await store.delete_role_binding(ModelType.ASSIST, 1)
    assert ok is True
    listed = await store.list_role_bindings(ModelType.ASSIST)
    assert [b.model for b in listed] == ["a", "c"]
    assert [b.priority for b in listed] == [0, 1]


async def test_delete_missing_returns_false(store):
    assert await store.delete_role_binding(ModelType.MAIN, 99) is False


async def test_reorder_bindings(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "a")
    await store.add_role_binding(ModelType.MAIN, "svc-b", "b")
    await store.add_role_binding(ModelType.MAIN, "svc-a", "c")
    result = await store.reorder_role_bindings(
        ModelType.MAIN,
        [("svc-b", "b"), ("svc-a", "c"), ("svc-a", "a")],
    )
    assert [(b.service_id, b.model, b.priority) for b in result] == [
        ("svc-b", "b", 0),
        ("svc-a", "c", 1),
        ("svc-a", "a", 2),
    ]


async def test_reorder_mismatch_raises(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "a")
    with pytest.raises(ValueError, match="不匹配"):
        await store.reorder_role_bindings(ModelType.MAIN, [("svc-a", "wrong-model")])


async def test_service_deletion_cascades_bindings(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "qwen-max")
    await store.add_role_binding(ModelType.MAIN, "svc-b", "claude")
    await store.delete_service("svc-a")
    listed = await store.list_role_bindings(ModelType.MAIN)
    assert [b.service_id for b in listed] == ["svc-b"]


# ─── resolve_role ────────────────────────────────────────

async def test_resolve_role_returns_decrypted_candidates(store):
    await store.add_role_binding(ModelType.RERANK, "svc-a", "rerank-v3")
    await store.add_role_binding(ModelType.RERANK, "svc-b", "rerank-v2")
    resolved = await store.resolve_role(ModelType.RERANK)
    assert len(resolved) == 2
    assert resolved[0].service_id == "svc-a"
    assert resolved[0].api_key == "sk-aaa"
    assert resolved[0].base_url == "https://a.example/v1"
    assert resolved[0].model == "rerank-v3"
    assert resolved[1].priority == 1


async def test_embedding_role_is_single_binding(store):
    """嵌入角色只允许一条绑定, 重复添加应报错."""
    b = await store.add_role_binding(ModelType.EMBEDDING, "svc-a", "embed-v3")
    assert b.priority == 0
    with pytest.raises(ValueError, match="嵌入模型只允许一条绑定"):
        await store.add_role_binding(ModelType.EMBEDDING, "svc-b", "embed-v2")
    # 删除后可再添
    await store.delete_role_binding(ModelType.EMBEDDING, 0)
    b2 = await store.add_role_binding(ModelType.EMBEDDING, "svc-b", "embed-v2")
    assert b2.priority == 0


async def test_embedding_reorder_rejected(store):
    await store.add_role_binding(ModelType.EMBEDDING, "svc-a", "embed-v3")
    with pytest.raises(ValueError, match="嵌入角色只允许一条绑定"):
        await store.reorder_role_bindings(
            ModelType.EMBEDDING, [("svc-a", "embed-v3")]
        )


async def test_role_binding_metadata_persistence(store):
    """context_length / embedding_dim 字段可存可读."""
    await store.add_role_binding(
        ModelType.MAIN, "svc-a", "qwen-max",
        context_length=131072,
    )
    await store.add_role_binding(
        ModelType.EMBEDDING, "svc-b", "embed-v3",
        embedding_dim=1024,
    )
    main = await store.list_role_bindings(ModelType.MAIN)
    assert main[0].context_length == 131072
    assert main[0].embedding_dim is None
    emb = await store.list_role_bindings(ModelType.EMBEDDING)
    assert emb[0].embedding_dim == 1024
    assert emb[0].context_length is None
    # resolve 也带过来
    resolved = await store.resolve_role(ModelType.EMBEDDING)
    assert resolved[0].embedding_dim == 1024


async def test_send_dimensions_defaults_false(store):
    """v0.2.8: send_dimensions 未指定时默认 False, 不透传上游."""
    await store.add_role_binding(
        ModelType.EMBEDDING, "svc-a", "bge-m3", embedding_dim=1024
    )
    listed = await store.list_role_bindings(ModelType.EMBEDDING)
    assert listed[0].send_dimensions is False
    resolved = await store.resolve_role(ModelType.EMBEDDING)
    assert resolved[0].send_dimensions is False
    assert resolved[0].embedding_dim == 1024


async def test_send_dimensions_persistence_true(store):
    """显式 send_dimensions=True 持久化 + resolve 带过来."""
    await store.add_role_binding(
        ModelType.EMBEDDING, "svc-a", "text-embedding-v3",
        embedding_dim=1024, send_dimensions=True,
    )
    listed = await store.list_role_bindings(ModelType.EMBEDDING)
    assert listed[0].send_dimensions is True
    resolved = await store.resolve_role(ModelType.EMBEDDING)
    assert resolved[0].send_dimensions is True


async def test_migration_adds_columns_to_legacy_table(tmp_path):
    """幂等迁移: 手工建旧 5 列 schema + 插一行, init_db 后新列为 NULL."""
    import aiosqlite

    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.execute(
            "CREATE TABLE llm_services (id TEXT PRIMARY KEY, base_url TEXT NOT NULL, "
            "api_key_encrypted TEXT NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE role_bindings ("
            "role TEXT NOT NULL, priority INTEGER NOT NULL, service_id TEXT NOT NULL, "
            "model TEXT NOT NULL, created_at TIMESTAMP NOT NULL, "
            "PRIMARY KEY (role, priority))"
        )
        await db.commit()

    s = LLMServiceStore(db_path)
    await s.init_db()
    # 手工插一条旧数据 (走底层, 不通过约束校验的 add_role_binding)
    await s.save_service(LLMServiceProvider.create("svc-a", "https://a", "sk-a"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO role_bindings (role, priority, service_id, model, created_at) "
            "VALUES ('main', 0, 'svc-a', 'legacy-model', '2026-01-01T00:00:00+00:00')"
        )
        await db.commit()

    listed = await s.list_role_bindings(ModelType.MAIN)
    assert len(listed) == 1
    assert listed[0].model == "legacy-model"
    assert listed[0].context_length is None
    assert listed[0].embedding_dim is None
    assert listed[0].send_dimensions is False


async def test_resolve_empty_role(store):
    assert await store.resolve_role(ModelType.RERANK) == []


# ─── RoleResolver 缓存 ────────────────────────────────────

async def test_resolver_caches_and_invalidates(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "m1")
    resolver = RoleResolver(store)
    first = await resolver.for_role(ModelType.MAIN)
    assert [c.model for c in first] == ["m1"]
    v1 = resolver.version

    # 直接改 DB, 不 invalidate → 缓存仍返回旧结果
    await store.add_role_binding(ModelType.MAIN, "svc-b", "m2")
    still_cached = await resolver.for_role(ModelType.MAIN)
    assert [c.model for c in still_cached] == ["m1"]

    resolver.invalidate(ModelType.MAIN)
    fresh = await resolver.for_role(ModelType.MAIN)
    assert [c.model for c in fresh] == ["m1", "m2"]
    assert resolver.version > v1


async def test_resolver_empty_role_raises(store):
    resolver = RoleResolver(store)
    with pytest.raises(NoCandidateForRoleError):
        await resolver.for_role(ModelType.MAIN)
    # require=False 允许空
    assert await resolver.for_role(ModelType.MAIN, require=False) == []


async def test_resolver_first_returns_top_priority(store):
    await store.add_role_binding(ModelType.MAIN, "svc-a", "primary")
    await store.add_role_binding(ModelType.MAIN, "svc-b", "backup")
    resolver = RoleResolver(store)
    top = await resolver.first(ModelType.MAIN)
    assert top.model == "primary"
