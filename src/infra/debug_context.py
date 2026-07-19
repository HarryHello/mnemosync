"""调试请求上下文.

用 contextvars 在整个 async 请求链条上传递 correlation_id + agent 名, 让
Forwarder 出去打上游时能把日志关联到最初的入站请求。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

_correlation_id: ContextVar[Optional[str]] = ContextVar("mnemosync_debug_cid", default=None)
_agent_name: ContextVar[Optional[str]] = ContextVar("mnemosync_debug_agent", default=None)


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
        self._token = None

    def __enter__(self):
        self._token = _agent_name.set(self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        _agent_name.reset(self._token)
        return False
