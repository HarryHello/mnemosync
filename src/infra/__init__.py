"""基础设施层: 外部交互（Forwarder / 向量存储 / 消息提取 / LLM 服务配置）."""

from .extraction import (
    extract_all_user_messages,
    extract_latest_user_message,
    extract_new_messages,
)
from .forwarder import (
    ConnectionPool,
    Forwarder,
    ForwarderConfig,
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
)

# NOTE (循环导入修复): 移除 VectorStore eager re-export — 它依赖 src.core.memory.models,
# 而 src.core.memory -> lifecycle -> src.core.models.resolver -> src.infra, 会造成 import 环.
# 调用方请直接: from src.infra.vector_store import VectorStore

__all__ = [
    "Forwarder",
    "ForwarderConfig",
    "ConnectionPool",
    "UpstreamError",
    "UpstreamTimeout",
    "parse_sse_stream",
    "extract_latest_user_message",
    "extract_all_user_messages",
    "extract_new_messages",
]
