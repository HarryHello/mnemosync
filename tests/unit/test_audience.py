"""受众过滤 (v0.3.0 Sub-Phase C) 测试.

覆盖 is_visible 全规则矩阵:
  * PUBLIC 任何上下文可见 (含非归属)
  * SOURCE_RESTRICTED 仅来源桶可见 — 同空间其他成员的也不行
  * 空间共享 (space_id 匹配 + 非 SOURCE_RESTRICTED) 对空间成员可见, 私聊不可见
  * FRIENDS_ONLY / CONFIDENTIAL 的关系门槛
  * custom_policies deny 一票否决 / allow 白名单
  * build_chromadb_where 的三种形态
"""

from __future__ import annotations

from src.core.memory.audience import (
    CONFIDENTIAL_TRUST_THRESHOLD,
    AudienceFilter,
    RetrievalContext,
)
from src.core.memory.models import MemoryEntry, Relationship, Visibility


def _entry(
    source_user: str | None = "alice",
    visibility: Visibility = Visibility.SOURCE_RESTRICTED,
    space_id: str | None = None,
    custom_policies: list[str] | None = None,
) -> MemoryEntry:
    e = MemoryEntry.create(content="x", role="user", source_user=source_user)
    e.visibility = visibility
    e.space_id = space_id
    e.custom_policies = custom_policies or []
    return e


def _ctx(
    user: str | None = "bob",
    space_id: str | None = None,
    actor_id: str | None = None,
    rel: Relationship | None = None,
) -> RetrievalContext:
    return RetrievalContext(
        effective_user_id=user,
        actor_id=actor_id,
        space_id=space_id,
        channel_type="group" if space_id else "direct",
        relationship=rel,
    )


def _rel(type_: str = "stranger", trust: float = 0.0) -> Relationship:
    r = Relationship.create("default", "bob")
    r.type = type_
    r.trust_level = trust
    return r


# ─── PUBLIC ────────────────────────────────────────────


def test_public_visible_everywhere() -> None:
    e = _entry(visibility=Visibility.PUBLIC)
    assert AudienceFilter.is_visible(e, _ctx(user="bob"))
    assert AudienceFilter.is_visible(e, _ctx(user="bob", space_id="g1"))
    # 非归属模式也能看 PUBLIC
    assert AudienceFilter.is_visible(e, _ctx(user=None))


# ─── SOURCE_RESTRICTED ────────────────────────────────


def test_source_restricted_own_bucket_visible() -> None:
    e = _entry(source_user="bob")
    assert AudienceFilter.is_visible(e, _ctx(user="bob"))


def test_source_restricted_other_bucket_hidden() -> None:
    e = _entry(source_user="alice")
    assert not AudienceFilter.is_visible(e, _ctx(user="bob"))


def test_source_restricted_hidden_in_shared_space() -> None:
    """群聊关键不变量: 其他成员的 SOURCE_RESTRICTED 记忆绝不泄露."""
    e = _entry(source_user="alice", space_id="g1")
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", space_id="g1"))


def test_own_private_memory_visible_in_group() -> None:
    """自己的私聊记忆 (无 space_id) 在群聊里自己也看得到."""
    e = _entry(source_user="bob", space_id=None)
    assert AudienceFilter.is_visible(e, _ctx(user="bob", space_id="g1"))


# ─── 空间共享 ──────────────────────────────────────────


def test_space_shared_visible_to_space_members() -> None:
    """space_id 匹配 + 非 SOURCE_RESTRICTED → 空间成员可见."""
    e = _entry(source_user="alice", visibility=Visibility.PUBLIC, space_id="g1")
    assert AudienceFilter.is_visible(e, _ctx(user="bob", space_id="g1"))


def test_space_shared_hidden_in_other_space() -> None:
    e = _entry(source_user="alice", visibility=Visibility.FRIENDS_ONLY, space_id="g1")
    # 另一个群, 且非好友 → 不可见
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", space_id="g2"))


def test_space_shared_hidden_in_private_chat() -> None:
    """群里的共享记忆不出现在私聊上下文 (私聊: 自己 + PUBLIC)."""
    e = _entry(source_user="alice", visibility=Visibility.FRIENDS_ONLY, space_id="g1")
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", space_id=None))


# ─── 关系门槛 ──────────────────────────────────────────


def test_friends_only_requires_friend_relationship() -> None:
    e = _entry(source_user="alice", visibility=Visibility.FRIENDS_ONLY)
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", rel=_rel("stranger")))
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", rel=_rel("acquaintance")))
    assert AudienceFilter.is_visible(e, _ctx(user="bob", rel=_rel("friend")))
    assert AudienceFilter.is_visible(e, _ctx(user="bob", rel=_rel("intimate")))
    # 无关系数据 → 不可见
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", rel=None))


def test_confidential_requires_trust_threshold() -> None:
    e = _entry(source_user="alice", visibility=Visibility.CONFIDENTIAL)
    assert not AudienceFilter.is_visible(
        e, _ctx(user="bob", rel=_rel("friend", trust=CONFIDENTIAL_TRUST_THRESHOLD - 0.01))
    )
    assert AudienceFilter.is_visible(
        e, _ctx(user="bob", rel=_rel("friend", trust=CONFIDENTIAL_TRUST_THRESHOLD))
    )


# ─── custom_policies ──────────────────────────────────


def test_deny_user_overrides_visibility() -> None:
    e = _entry(source_user="alice", visibility=Visibility.PUBLIC,
               custom_policies=["deny:user:bob"])
    assert not AudienceFilter.is_visible(e, _ctx(user="bob"))
    assert AudienceFilter.is_visible(e, _ctx(user="carol"))


def test_deny_actor() -> None:
    e = _entry(source_user="alice", visibility=Visibility.PUBLIC,
               custom_policies=["deny:actor:actor-qq-bob"])
    assert not AudienceFilter.is_visible(e, _ctx(user="bob", actor_id="actor-qq-bob"))
    assert AudienceFilter.is_visible(e, _ctx(user="bob", actor_id="actor-web-bob"))


def test_allow_whitelist() -> None:
    e = _entry(source_user="alice", visibility=Visibility.PUBLIC,
               custom_policies=["allow:user:bob"])
    assert AudienceFilter.is_visible(e, _ctx(user="bob"))
    assert not AudienceFilter.is_visible(e, _ctx(user="carol"))


# ─── 非归属模式 ────────────────────────────────────────


def test_unattributed_sees_only_public() -> None:
    assert not AudienceFilter.is_visible(_entry(), _ctx(user=None))
    assert not AudienceFilter.is_visible(
        _entry(visibility=Visibility.FRIENDS_ONLY), _ctx(user=None)
    )
    assert AudienceFilter.is_visible(
        _entry(visibility=Visibility.PUBLIC), _ctx(user=None)
    )


# ─── filter / build_chromadb_where ────────────────────


def test_filter_keeps_only_visible() -> None:
    entries = [
        _entry(source_user="bob"),                                   # 自己的
        _entry(source_user="alice"),                                 # 别人的私有
        _entry(source_user="alice", visibility=Visibility.PUBLIC),   # 公共
    ]
    kept = AudienceFilter.filter(entries, _ctx(user="bob"))
    assert len(kept) == 2
    assert entries[1] not in kept


def test_where_unattributed_public_only() -> None:
    where = AudienceFilter.build_chromadb_where(_ctx(user=None))
    assert where == {"visibility": "public"}


def test_where_private_chat() -> None:
    where = AudienceFilter.build_chromadb_where(_ctx(user="bob"))
    assert where == {"$or": [{"source_user": "bob"}, {"visibility": "public"}]}


def test_where_group_chat_includes_space() -> None:
    where = AudienceFilter.build_chromadb_where(_ctx(user="bob", space_id="g1"))
    assert where == {
        "$or": [
            {"source_user": "bob"},
            {"visibility": "public"},
            {"space_id": "g1"},
        ]
    }
