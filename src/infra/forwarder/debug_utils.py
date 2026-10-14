"""调试工具函数: 转发器共用的调试事件发射."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.infra.debug_context import get_agent_name, get_correlation_id

from .debug_hook import get_debug_bus

# http_log_store (beta.27): 上游 agent 调用的常驻持久化 sink —
# 与调试开关无关, 每次上游调用都落一行 (direction='upstream'),
# 供请求日志的链路侧板回看完整管线. 生命周期由 lifespan 管理.
_http_log_sink: Any | None = None


def set_http_log_sink(sink: Any | None) -> None:
    """注册 http_log_store (enqueue 兼容对象); 关停时传 None."""
    global _http_log_sink
    _http_log_sink = sink


def emit_upstream_debug(direction: str, url: str, **fields: Any) -> str | None:
    """发射上游调试事件.

    Args:
        direction: 事件方向 (upstream_request / upstream_response)
        url: 请求 URL
        **fields: 额外调试字段

    Returns:
        事件 ID (用于后续追加流式数据), 未发射时返回 None
    """
    cid = get_correlation_id() or "no-cid"
    agent = get_agent_name()

    # 常驻持久化 (beta.27): 不受调试开关影响 — 请求日志侧板要能回看
    # 历史请求的完整管线, 内存调试总线重启即失, 不能作为唯一载体.
    sink = _http_log_sink
    if sink is not None:
        is_request = direction == "upstream_request"
        body = fields.get("body")
        try:
            sink.enqueue({
                "method": fields.get("method", "POST"),
                "path": url,
                "query_params": None,
                "request_headers": None,
                "request_body": body if is_request else None,
                "response_status": fields.get("status"),
                "response_body": None if is_request else body,
                "duration_ms": fields.get("duration_ms"),
                "client_ip": None,
                "correlation_id": cid,
                "direction": "upstream",
                "agent": agent,
            })
        except Exception:  # 日志 sink 永不阻断请求
            pass

    bus = get_debug_bus()
    if bus is None or not bus.should_emit():
        return None
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
