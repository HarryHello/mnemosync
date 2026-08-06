"""受众过滤 (v0.3.0 Sub-Phase C).

多用户场景下, 记忆检索必须按"谁能看"过滤:

  * 私聊: 自己的记忆 (source_user == 自己的桶) + PUBLIC
  * 群聊 (space): 自己的记忆 + PUBLIC + 本空间共享记忆
    (space_id 匹配且非 SOURCE_RESTRICTED); 绝不看其他参与者的私有记忆
  * SOURCE_RESTRICTED: 仅来源用户桶可见 (默认可见性)
  * FRIENDS_ONLY / CONFIDENTIAL: 非来源用户需要关系门槛
    (friend/intimate 类型 或 信任度 >= 0.7)
  * custom_policies: deny:user:<id> / deny:actor:<id> 一票否决;
    存在 allow:* 规则时构成白名单
  * 非归属模式 (无 effective_user_id): 仅 PUBLIC

检索链路: ChromaDB 粗筛用 `build_chromadb_where` 放宽到"可能可见"的
超集 ($or: 自己 / public / 本空间), 精筛在拿到 MemoryEntry 后走
`AudienceFilter.is_visible` (关系门槛与策略只能在这里判)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.memory.models import Visibility

if TYPE_CHECKING:
    from src.core.memory.models import MemoryEntry, Relationship

# 关系门槛
FRIEND_TYPES = ("friend", "intimate")
CONFIDENTIAL_TRUST_THRESHOLD = 0.7


@dataclass(frozen=True)
class RetrievalContext:
    """一次检索的受众上下文: 谁在看, 在哪看."""

    effective_user_id: str | None      # 查看者的用户桶 (None = 非归属模式)
    actor_id: str | None = None        # 查看者的 Actor ID (custom_policies 用)
    space_id: str | None = None        # 当前会话空间 (None = 私聊/无空间)
    channel_type: str | None = None    # "direct" | "group" | None
    relationship: Relationship | None = None  # 查看者↔人格关系 (门槛判定用)


class AudienceFilter:
    """记忆可见性判定. 全部为纯函数, 便于测试."""

    @staticmethod
    def is_visible(entry: MemoryEntry, ctx: RetrievalContext) -> bool:
        """判定一条记忆对 ctx 的查看者是否可见."""
        # 1. custom_policies: deny 一票否决; allow 白名单
        if not AudienceFilter._check_policies(entry.custom_policies, ctx):
            return False

        # 2. PUBLIC: 任何上下文可见
        if entry.visibility == Visibility.PUBLIC:
            return True

        viewer = ctx.effective_user_id
        # 3. 非归属模式: 仅 PUBLIC (已在上面放行), 其余一律不可见
        if not viewer:
            return False

        # 4. 自己桶的记忆: 可见 (任何可见性 — 自己的私聊记忆在群聊里自己也看得到)
        if entry.source_user == viewer:
            return True

        # 5. 空间共享: space_id 匹配且非 SOURCE_RESTRICTED
        if (
            entry.space_id
            and ctx.space_id
            and entry.space_id == ctx.space_id
            and entry.visibility != Visibility.SOURCE_RESTRICTED
        ):
            return True

        # 6. 关系门槛 (非来源用户)
        rel = ctx.relationship
        if entry.visibility == Visibility.FRIENDS_ONLY:
            return rel is not None and rel.type in FRIEND_TYPES
        if entry.visibility == Visibility.CONFIDENTIAL:
            return rel is not None and rel.trust_level >= CONFIDENTIAL_TRUST_THRESHOLD

        # 7. SOURCE_RESTRICTED 且非来源用户: 不可见
        return False

    @staticmethod
    def filter(
        entries: list[MemoryEntry], ctx: RetrievalContext,
    ) -> list[MemoryEntry]:
        """过滤一批记忆, 只保留查看者可见的."""
        return [e for e in entries if AudienceFilter.is_visible(e, ctx)]

    @staticmethod
    def build_chromadb_where(ctx: RetrievalContext) -> dict[str, Any] | None:
        """构建 ChromaDB 粗筛 where 子句 (可见超集, 精筛交给 is_visible).

        非归属: 仅 public.
        有用户: $or[自己桶, public, (群聊时) 本空间].
        """
        if not ctx.effective_user_id:
            return {"visibility": Visibility.PUBLIC.value}
        conditions: list[dict[str, Any]] = [
            {"source_user": ctx.effective_user_id},
            {"visibility": Visibility.PUBLIC.value},
        ]
        if ctx.space_id:
            conditions.append({"space_id": ctx.space_id})
        return {"$or": conditions}

    @staticmethod
    def _check_policies(policies: list[str], ctx: RetrievalContext) -> bool:
        """评估 custom_policies. deny 优先; 存在 allow 规则则构成白名单."""
        if not policies:
            return True
        viewer = ctx.effective_user_id
        actor = ctx.actor_id
        allow_rules: list[str] = []
        for p in policies:
            if p.startswith("deny:"):
                if (
                    (viewer and p == f"deny:user:{viewer}")
                    or (actor and p == f"deny:actor:{actor}")
                ):
                    return False
            elif p.startswith("allow:"):
                allow_rules.append(p)
        if allow_rules:
            return any(
                (viewer and p == f"allow:user:{viewer}")
                or (actor and p == f"allow:actor:{actor}")
                for p in allow_rules
            )
        return True
