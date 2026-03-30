"""API Key 数据模型."""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ApiKey:
    """API Key 数据模型."""

    id: str
    key_hash: str  # 存储哈希值而非明文
    key_prefix: str  # 存储前 12 位用于识别 (如 sk-abc12345...)
    note: str  # 备注
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    is_active: bool = True
    key_full: str | None = None  # 存储完整 key (仅生成时保存一次，用于显示)

    @staticmethod
    def generate(note: str) -> "ApiKey":
        """生成新的 API Key."""
        raw_key = f"sk-{secrets.token_urlsafe(32)}"
        key_hash = secrets.token_hex(32)  # 用于验证的哈希
        key_prefix = raw_key[:12]  # 存储前 12 位用于展示

        return ApiKey(
            id=secrets.token_hex(16),
            key_hash=key_hash,
            key_prefix=key_prefix,
            note=note,
            key_full=raw_key,  # 仅在此时保存完整 key
        )

    def mark_used(self) -> None:
        """标记为已使用."""
        self.last_used_at = datetime.now(timezone.utc)

    def deactivate(self) -> None:
        """停用 API Key."""
        self.is_active = False
