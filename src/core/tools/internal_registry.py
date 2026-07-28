"""内部 tool 注册表.

Mnemosync 向主模型注入的内部工具, 与客户端提供的 tools 分离。
模型调用内部 tool 时, Mnemosync 在出站拦截, 服务端执行, 合成 tool_result,
再调一轮 LLM 生成自然回复。客户端永远看不到内部 tool_calls。

架构:
  1. InternalTool: 定义 (name, description, parameters JSON schema) + handler
  2. registry: 全局注册表, lifespan 初始化时加载
  3. 注入: forward 路径把内部 tools 合并进 tools 列表传给上游 LLM
  4. 拦截: 出站 tool_calls 中属于内部 tool 的, 执行 handler, 不返回给客户端
  5. 重调: 内部 tool 执行完毕后, 把 tool_result 加入 messages, 再调一轮 LLM
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InternalTool:
    """一个内部工具定义."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Awaitable[dict[str, Any]]]

    def to_openai_tool(self) -> dict[str, Any]:
        """转为 OpenAI tools 格式."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class InternalToolRegistry:
    """内部工具注册表."""

    def __init__(self) -> None:
        self._tools: dict[str, InternalTool] = {}

    def register(self, tool: InternalTool) -> None:
        self._tools[tool.name] = tool
        logger.debug("注册内部 tool: %s", tool.name)

    def get(self, name: str) -> InternalTool | None:
        return self._tools.get(name)

    @property
    def names(self) -> set[str]:
        return set(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """返回所有内部 tool 的 OpenAI 格式定义."""
        return [t.to_openai_tool() for t in self._tools.values()]

    def is_empty(self) -> bool:
        return len(self._tools) == 0


# 全局单例
_registry: InternalToolRegistry | None = None


def get_internal_tool_registry() -> InternalToolRegistry:
    global _registry
    if _registry is None:
        _registry = InternalToolRegistry()
    return _registry


def set_internal_tool_registry(registry: InternalToolRegistry) -> None:
    global _registry
    _registry = registry
