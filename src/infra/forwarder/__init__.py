"""Forwarder: 所有模型调用的唯一 HTTP 出口.

``MultiForwarder`` 依赖 ``src.core.models.resolver``, 后者又反向依赖
``src.infra.llm_service``, 直接在此包 ``__init__`` re-export 会造成循环导入.
因此显式从 ``src.infra.forwarder.multi`` 导入即可.
"""

from .connection_pool import ConnectionPool
from .forwarder import (
    Forwarder,
    ForwarderConfig,
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
)

__all__ = [
    "Forwarder",
    "ForwarderConfig",
    "ConnectionPool",
    "UpstreamError",
    "UpstreamTimeout",
    "parse_sse_stream",
]
