"""Forwarder → 调试事件 hook.

Forwarder 层不能直接依赖 FastAPI 的 app.state (它是纯 httpx 客户端), 所以用
一个模块级 setter 让 lifespan 在启动时把 DebugEventBus 单例注入进来。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.infra.debug_bus import DebugEventBus

_bus: Optional["DebugEventBus"] = None


def set_debug_bus(bus: "DebugEventBus | None") -> None:
    global _bus
    _bus = bus


def get_debug_bus() -> "DebugEventBus | None":
    return _bus
