"""记忆数据模型."""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Visibility(str, Enum):
    """记忆可见性."""

    PUBLIC = "public"  # 公开，所有用户可见
    FRIENDS_ONLY = "friends_only"  # 仅好友可见
    CONFIDENTIAL = "confidential"  # 仅高信任度用户可见
    SOURCE_RESTRICTED = "source_restricted"  # 仅来源用户可见 (默认)


@dataclass
class MemoryEntry:
    """记忆条目.

    Attributes:
        id: 唯一标识
        content: 记忆内容
        role: 消息角色 (user/assistant/system)
        source_user: 来源用户标识 (如 "马达" 或 API Key 关联的用户)
        visibility: 可见性
        custom_policies: 自定义策略 (如 "deny:user:A", "allow:user:B")
        emotional_tags: 情感标签 (如 ["happy", "stress"])
        created_at: 创建时间
        last_accessed: 最后访问时间
        expires_at: 过期时间 (可选，用于临时记忆)
    """

    id: str
    content: str
    role: str  # user, assistant, system
    source_user: str | None = None
    visibility: Visibility = Visibility.SOURCE_RESTRICTED
    custom_policies: list[str] = field(default_factory=list)
    emotional_tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime | None = None
    expires_at: datetime | None = None

    @staticmethod
    def create(
        content: str,
        role: str,
        source_user: str | None = None,
        visibility: Visibility = Visibility.SOURCE_RESTRICTED,
    ) -> "MemoryEntry":
        """创建新记忆条目.

        Args:
            content: 记忆内容
            role: 消息角色
            source_user: 来源用户标识
            visibility: 可见性

        Returns:
            新创建的记忆条目
        """
        return MemoryEntry(
            id=secrets.token_hex(16),
            content=content,
            role=role,
            source_user=source_user,
            visibility=visibility,
        )

    def mark_accessed(self) -> None:
        """标记为已访问."""
        self.last_accessed = datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        """检查是否过期."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at
