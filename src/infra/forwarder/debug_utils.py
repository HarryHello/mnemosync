"""调试工具函数: 转发器共用的调试事件发射."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.infra.debug_context import get_agent_name, get_correlation_id

from .debug_hook import get_debug_bus


def emit_upstream_debug(direction: str, url: str, **fields: Any) -> str | None:
    """发射上游调试事件.

    Args:
        direction: 事件方向 (upstream_request / upstream_response)
        url: 请求 URL
        **fields: 额外调试字段

    Returns:
        事件 ID (用于后续追加流式数据), 未发射时返回 None
    """
    bus = get_debug_bus()
    if bus is None or not bus.should_emit():
        return None
    cid = get_correlation_id() or "no-cid"
    agent = get_agent_name()
    parsed = urlparse(url)
    port = parsed.port
    return bus.emit(
        direction=direction,
        correlation_id=cid,
        url=url,
        port=port,
        agent=agent,
        **fields,
    )
