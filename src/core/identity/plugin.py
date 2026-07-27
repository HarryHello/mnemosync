"""身份解析插件接口 (v0.3.1).

插件负责把平台私有格式解析为两种产物:

* ``model_messages``: 供主模型消费的标准 OpenAI 消息;
* ``events``: 按说话者拆分的结构化事件, 供 Mnemosync 去重持久化。

插件协议不兼容早期仅返回 ``list[dict]`` 的 ``preprocess()`` 实现。第三方插件
必须显式返回 ``PluginPreprocessResult``，避免核心管线猜测返回值形状。
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
        store: "SqliteIdentityStore",
    ) -> PluginResult | None:
        """从原始消息中提取当前请求者与空间身份."""
        ...

    @abstractmethod
    async def preprocess(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: "SqliteIdentityStore",
        identity: "IdentityContext",
    ) -> PluginPreprocessResult:
        """生成模型消息与逐说话者规范化事件."""
        ...
