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
    await store.add_role_binding(ModelType.EMBEDDING, "svc-a", "text-embed-v3")
    await store.add_role_binding(ModelType.EMBEDDING, "svc-b", "text-embed-v2")
    resolved = await store.resolve_role(ModelType.EMBEDDING)
    assert len(resolved) == 2
    assert resolved[0].service_id == "svc-a"
    assert resolved[0].api_key == "sk-aaa"
    assert resolved[0].base_url == "https://a.example/v1"
    assert resolved[0].model == "text-embed-v3"
    assert resolved[1].priority == 1


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
