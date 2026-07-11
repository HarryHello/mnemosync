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

# vector_store 依赖 chromadb, 延迟导入避免未安装时整体不可用
try:
    from .vector_store import VectorStore
except ImportError:  # pragma: no cover
    VectorStore = None  # type: ignore[assignment,misc]

__all__ = [
    "Forwarder",
    "ForwarderConfig",
    "ConnectionPool",
    "UpstreamError",
    "UpstreamTimeout",
    "parse_sse_stream",
    "VectorStore",
    "extract_latest_user_message",
    "extract_all_user_messages",
    "extract_new_messages",
]
