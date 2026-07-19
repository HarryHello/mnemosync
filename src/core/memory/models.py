"""记忆领域核心数据模型.

定义记忆分类、衰减状态、关系状态等核心数据结构.
所有 Agent 通过这些模型交互.
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import log


class MemoryType(str, Enum):
    """记忆类型.

    - PERMANENT: 永久记忆，不衰减，除非被覆盖或删除
    - NORMAL: 普通记忆，遵循衰减模型，优先级随时间降低
    """

    PERMANENT = "permanent"
    NORMAL = "normal"


class Visibility(str, Enum):
    """记忆可见性.

    - PUBLIC: 所有用户可见
    - FRIENDS_ONLY: 仅好友可见
    - CONFIDENTIAL: 仅高信任度用户可见
    - SOURCE_RESTRICTED: 仅来源用户可见（默认）
    """

    PUBLIC = "public"
    FRIENDS_ONLY = "friends_only"
    CONFIDENTIAL = "confidential"
    SOURCE_RESTRICTED = "source_restricted"


class DecayState(str, Enum):
    """衰减状态（按有效优先级区间划分）.

    - ACTIVE: 优先级 > 0.3，保持在上下文中
    - DORMANT: 优先级 0.1-0.3，不主动加载，检索可召回
    - WEAK: 优先级 0.05-0.1，仅高相似度检索可召回
    - FORGOTTEN: 优先级 <= 0.05，标记遗忘（不删除，搜索可恢复）
    """

    ACTIVE = "active"
    DORMANT = "dormant"
    WEAK = "weak"
    FORGOTTEN = "forgotten"

    @classmethod
    def from_priority(cls, priority: float) -> "DecayState":
        """根据有效优先级推断衰减状态."""
        if priority > 0.3:
            return cls.ACTIVE
        elif priority > 0.1:
            return cls.DORMANT
        elif priority > 0.05:
            return cls.WEAK
        else:
            return cls.FORGOTTEN


# 衰减速率 → 半衰期天数映射（decay_rate 越大，衰减越快）
# 0.0 表示永不过期（用于永久记忆）
DECAY_RATE_TO_HALF_LIFE: dict[float, int | None] = {
    0.0: None,  # 永不过期
    0.05: 182,  # 长期偏好、习惯
    0.1: 91,    # 一般偏好、事实信息
    0.3: 33,    # 中期事件、计划
    0.5: 51,    # 一般事件、状态
    0.7: 17,    # 短期事件
    0.9: 11,    # 临时信息、情绪波动
}

# 遗忘阈值
FORGET_THRESHOLD = 0.05
WEAK_THRESHOLD = 0.1
DORMANT_THRESHOLD = 0.3
ACTIVE_THRESHOLD = 0.3

# 访问加成系数
ACCESS_BONUS_FACTOR = 0.05


def decay_rate_to_half_life(decay_rate: float) -> int | None:
    """将 decay_rate 映射到半衰期天数.

    Args:
        decay_rate: 衰减速率（0.0-1.0）

    Returns:
        半衰期天数，None 表示永不过期
    """
    if decay_rate <= 0.0:
        return None
    closest = min(
        DECAY_RATE_TO_HALF_LIFE.keys(),
        key=lambda k: abs(k - decay_rate),
    )
    return DECAY_RATE_TO_HALF_LIFE[closest]


@dataclass
class MemoryEntry:
    """记忆条目.

    一条结构化记忆，由记忆分析 Agent 提取并打标签后产生.

    Attributes:
        id: 唯一标识
        content: 记忆文本内容
        role: 消息角色（user/assistant/system）
        source_user: 来源用户标识
        memory_type: 记忆类型（永久/普通）
        importance: 基础重要性（0.0-1.0）
        decay_rate: 衰减速率（0.0=不衰减，1.0=快速衰减）
        priority: 当前有效优先级（由衰减模型计算或 Agent 覆盖）
        access_count: 被检索/访问的次数
        is_forgotten: 是否已标记遗忘（不删除，搜索可恢复）
        visibility: 可见性
        custom_policies: 自定义访问策略（如 "deny:user:A"）
        emotional_tags: 情感标签（如 ["stress", "health"]）
        related_memories: 关联的其他记忆 ID 列表
        created_at: 创建时间
        last_accessed: 最后访问时间
        expires_at: 过期时间（可选）
    """

    id: str
    content: str
    role: str
    source_user: str | None = None
    memory_type: MemoryType = MemoryType.NORMAL
    importance: float = 0.5
    decay_rate: float = 0.3
    priority: float = 0.5
    access_count: int = 0
    is_forgotten: bool = False
    visibility: Visibility = Visibility.SOURCE_RESTRICTED
    custom_policies: list[str] = field(default_factory=list)
    emotional_tags: list[str] = field(default_factory=list)
    related_memories: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime | None = None
    expires_at: datetime | None = None

    @staticmethod
    def create(
        content: str,
        role: str,
        source_user: str | None = None,
        memory_type: MemoryType = MemoryType.NORMAL,
        importance: float = 0.5,
        decay_rate: float = 0.3,
    ) -> "MemoryEntry":
        """创建新记忆条目.

        Args:
            content: 记忆文本
            role: 消息角色
            source_user: 来源用户
            memory_type: 记忆类型
            importance: 基础重要性
            decay_rate: 衰减速率

        Returns:
            新记忆条目（priority 初始化为 importance）
        """
        return MemoryEntry(
            id=f"mem_{secrets.token_hex(12)}",
            content=content,
            role=role,
            source_user=source_user,
            memory_type=memory_type,
            importance=importance,
            decay_rate=decay_rate,
            priority=1.0 if memory_type == MemoryType.PERMANENT else importance,
        )

    @property
    def is_permanent(self) -> bool:
        """是否为永久记忆."""
        return self.memory_type == MemoryType.PERMANENT

    @property
    def is_expired(self) -> bool:
        """是否已过期."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def decay_state(self) -> DecayState:
        """根据当前 priority 推断衰减状态."""
        if self.is_permanent:
            return DecayState.ACTIVE
        return DecayState.from_priority(self.priority)

    def compute_theoretical_priority(self) -> float:
        """按公式计算理论优先级（公式兜底，不含 Agent 覆盖）.

        公式: importance × 衰减因子 × 过期惩罚 + 访问加成
        - 衰减因子 = 0.5 ^ (经过天数 / 半衰期)
        - 过期惩罚 = 0.01 (过期) 或 1.0 (未过期)
        - 访问加成 = log(access_count + 1) × 0.05

        Returns:
            理论优先级（0.0-1.0）
        """
        if self.is_permanent:
            return 1.0

        now = datetime.now(timezone.utc)
        days_elapsed = max(0, (now - self.created_at).days)

        half_life = decay_rate_to_half_life(self.decay_rate)
        if half_life is None or half_life == 0:
            time_factor = 1.0
        else:
            time_factor = 0.5 ** (days_elapsed / half_life)

        expiration_penalty = 0.01 if self.is_expired else 1.0
        access_bonus = log(self.access_count + 1) * ACCESS_BONUS_FACTOR

        priority = self.importance * time_factor * expiration_penalty + access_bonus
        return min(1.0, max(0.0, priority))

    def mark_accessed(self) -> None:
        """标记为已访问（access_count + 1, 更新 last_accessed）."""
        self.access_count += 1
        self.last_accessed = datetime.now(timezone.utc)

    def mark_forgotten(self) -> None:
        """标记为遗忘."""
        self.is_forgotten = True

    def override_priority(self, new_priority: float) -> None:
        """Agent 覆盖优先级（衰减评估后调用）.

        Args:
            new_priority: 新的优先级（0.0-1.0）
        """
        self.priority = min(1.0, max(0.0, new_priority))
        self.is_forgotten = self.decay_state == DecayState.FORGOTTEN


@dataclass
class Relationship:
    """用户与 AI 人格的关系状态.

    由关系分析 Agent 维护，主对话 Agent 加载用于上下文.

    Attributes:
        persona_id: 人格标识
        user_id: 用户标识
        type: 关系类型（stranger/acquaintance/friend/intimate）
        intimacy_score: 亲密度（0.0-1.0）
        trust_level: 信任度（0.0-1.0）
        interaction_count: 互动次数
        last_active: 最后活跃时间
        notes: 备注（由关系分析 Agent 总结）
    """

    persona_id: str
    user_id: str
    type: str = "stranger"  # stranger | acquaintance | friend | intimate
    intimacy_score: float = 0.0
    trust_level: float = 0.0
    interaction_count: int = 0
    last_active: datetime | None = None
    notes: str = ""
    # v0.2.10: 动态称呼演化. None = 沿用 TOML 基线 (settings.persona.relation)
    persona_addressing: str | None = None
    user_addressing: str | None = None
    context: str | None = None

    @staticmethod
    def create(persona_id: str, user_id: str) -> "Relationship":
        """为新用户创建初始关系状态."""
        return Relationship(
            persona_id=persona_id,
            user_id=user_id,
            type="stranger",
            intimacy_score=0.0,
            trust_level=0.0,
            interaction_count=0,
            last_active=datetime.now(timezone.utc),
        )

    def apply_delta(
        self,
        intimacy_delta: float,
        trust_delta: float,
        new_type: str | None = None,
        notes: str | None = None,
    ) -> None:
        """应用关系分析 Agent 计算出的增量.

        Args:
            intimacy_delta: 亲密度变化（可正可负）
            trust_delta: 信任度变化
            new_type: 新关系类型（可选，由 Agent 决定是否升级）
            notes: 备注更新（可选）
        """
        self.intimacy_score = min(1.0, max(0.0, self.intimacy_score + intimacy_delta))
        self.trust_level = min(1.0, max(0.0, self.trust_level + trust_delta))
        self.interaction_count += 1
        self.last_active = datetime.now(timezone.utc)
        if new_type is not None:
            self.type = new_type
        if notes is not None:
            self.notes = notes


@dataclass
class DecayEvaluation:
    """记忆衰减评估结果（记忆分析 Agent 产出）.

    对每条需评估的已有普通记忆，Agent 输出此结构.
    """

    memory_id: str
    current_priority: float
    new_priority: float
    decision: DecayState
    factors: dict[str, float]  # time_factor, access_bonus, emotional_factor, relation_factor
    reflection: str  # Agent 的自检说明


@dataclass
class CandidateMemory:
    """记忆分析 Agent 提取的新记忆候选.

    Agent 输出此结构，由 vector_index 节点入库.
    """

    content: str
    role: str
    memory_type: MemoryType
    importance: float
    decay_rate: float
    emotional_tags: list[str]
    expires_at: datetime | None = None
    overrides: str | None = None  # 被覆盖的已有记忆 ID
    related_to: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class RelationshipAuditEntry:
    """关系称呼字段的变更审计记录 (v0.2.10).

    每次 Agent 或人工修改 relationships 表的 persona_addressing / user_addressing /
    context 字段都会追加一条; 一次调用改多字段则写多条 (便于按字段查询/回退).
    """

    id: int  # 数据库 AUTOINCREMENT 分配, 未持久化前可为 0
    persona_id: str
    user_id: str
    changed_at: datetime
    source: str  # 'agent' | 'manual'
    field_name: str  # 'persona_addressing' | 'user_addressing' | 'context'
    old_value: str | None
    new_value: str | None
    reason: str
