"""调试请求上下文.

用 contextvars 在整个 async 请求链条上传递 correlation_id + agent 名, 让
Forwarder 出去打上游时能把日志关联到最初的入站请求。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.debug_bus import DebugEventBus

logger = logging.getLogger(__name__)

_correlation_id: ContextVar[str | None] = ContextVar("mnemosync_debug_cid", default=None)
_agent_name: ContextVar[str | None] = ContextVar("mnemosync_debug_agent", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(cid: str) -> None:
    _correlation_id.set(cid)


def get_agent_name() -> str | None:
    return _agent_name.get()


def set_agent_name(name: str | None) -> None:
    _agent_name.set(name)


class use_agent:
    """with 语句临时挂 agent 名."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._token: Token[str | None] | None = None

    def __enter__(self) -> use_agent:
        token = _agent_name.set(self.name)
        self._token = token
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if self._token is not None:
            _agent_name.reset(self._token)


def emit_pipeline(
    bus: DebugEventBus | None,
    *,
    event_kind: str,
    **data: object,
) -> None:
    """安全发射管线调试事件.

    bus 为 None 或无订阅者时静默跳过, 不影响主流程.
    correlation_id 从 contextvars 读取。
    """
    if bus is None:
        return
    cid = _correlation_id.get()
    if not cid:
        return
    try:
        bus.emit_pipeline(
            correlation_id=cid,
            event_kind=event_kind,
            data={k: v for k, v in data.items() if v is not None},
        )
    except Exception:
        logger.debug("emit_pipeline 失败 (不影响主流程)", exc_info=True)
