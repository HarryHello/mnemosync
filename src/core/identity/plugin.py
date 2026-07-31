"""身份解析插件接口 (v0.3.1).

插件负责从平台私有格式中提取身份信息。

必须实现:
- ``extract()``: 从原始消息中识别当前请求者身份。

可选实现:
- ``preprocess()``: 将消息转换为模型可消费格式 + 拆分为逐说话者事件。
  默认实现直接返回原消息和空事件列表，适用于不需要消息预处理的平台。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.identity.models import IdentityContext
    from src.persistence.identity_store import SqliteIdentityStore


@dataclass
class PluginResult:
    """插件提取出的当前请求身份."""

    external_key: str
    display_name: str | None = None
    space_id: str | None = None
    channel_type: str | None = None
    external_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEvent:
    """平台消息规范化后的单说话者事件."""

    role: str
    content: str
    source_frontend: str
    origin: str  # current | history_snapshot
    source_timestamp: datetime | None = None
    actor_id: str | None = None
    effective_user_id: str | None = None
    display_name: str | None = None
    external_key: str | None = None
    space_id: str | None = None
    external_event_id: str | None = None


@dataclass
class PluginPreprocessResult:
    """插件预处理的强类型返回值."""

    model_messages: list[dict[str, Any]]
    events: list[NormalizedEvent]

    @property
    def current_event(self) -> NormalizedEvent | None:
        """返回本轮真实新事件；每次请求至多应有一个."""
        return next((event for event in reversed(self.events) if event.origin == "current"), None)


class IdentityPlugin(ABC):
    """平台身份与消息规范化插件基类."""

    name: str = ""
    description: str = ""

    @abstractmethod
    async def extract(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: SqliteIdentityStore,
    ) -> PluginResult | None:
        """从原始消息中提取当前请求者与空间身份."""
        ...

    async def preprocess(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: SqliteIdentityStore,
        identity: IdentityContext,
    ) -> PluginPreprocessResult:
        """生成模型消息与逐说话者规范化事件.

        默认实现直接返回原消息和空事件列表。
        需要消息预处理的平台 (如群聊快照拆分) 应覆写此方法。
        """
        return PluginPreprocessResult(model_messages=messages, events=[])
