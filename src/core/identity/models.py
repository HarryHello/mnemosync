"""身份领域数据模型 (v0.3.0).

定义 Actor、UserGroup、IdentityStrategy、IdentityContext 等核心数据结构.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class StrategyType(StrEnum):
    """身份识别策略类型."""

    DIRECT = "direct"           # 使用 request.user 字段
    API_KEY_BOUND = "api_key_bound"  # 固定身份
    REGEX = "regex"             # 从消息内容正则提取
    LLM = "llm"                 # 用辅助模型提取
    PLUGIN = "plugin"           # 第三方插件 (v0.3.1)


@dataclass
class Actor:
    """前台应用上的一个可识别账号.

    由 (frontend, external_key) 唯一确定.
    frontend: 前台应用名 (AstrBot, MaiBot, ChatBox, Web 等)
    external_key: 平台侧标识 (QQ 号, Discord ID, 用户名等)
    """

    id: str
    external_key: str
    frontend: str
    display_name: str | None = None
    metadata: str = "{}"  # JSON
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class UserGroup:
    """一个真实人。管理员将多个 Actor 绑定到同一个 UserGroup。"""

    id: str
    name: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ActorGroupMembership:
    """Actor 到 UserGroup 的绑定。"""

    actor_id: str
    group_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class IdentityStrategy:
    """身份识别策略。

    绑定到 API Key，定义如何从请求中提取身份信息。
    """

    id: str
    name: str
    strategy_type: str  # 'direct' | 'api_key_bound' | 'regex' | 'llm'
    config: str = "{}"  # JSON
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class IdentityContext:
    """从请求中解析出的身份信息。"""

    actor_id: str | None          # 参与者 ID (None = 非归属模式)
    actor: Actor | None           # 完整 Actor 对象
    frontend: str | None          # 前台应用名
    external_key: str | None      # 平台侧标识
    display_name: str | None      # 显示名称
    space_id: str | None          # 会话空间 ID
    channel_type: str | None      # "direct" | "group" | None
    strategy_name: str | None     # 使用的策略名（调试用）
    external_event_id: str | None = None  # 平台侧事件唯一 ID (幂等用)
    effective_user_id: str | None = None  # 实际用户 ID (group_id 或 actor_id); None = 非归属模式
