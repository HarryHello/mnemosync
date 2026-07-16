"""角色 → 候选模型列表 解析器.

单一真相源: ``llm_service.db`` 的 ``role_bindings`` 表.
提供内存缓存 + 版本号失效, 修改绑定后调用 ``invalidate()`` 让下一次请求拿到新列表.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from src.infra.llm_service.models import ModelType, ResolvedCandidate
from src.infra.llm_service.store import LLMServiceStore


class NoCandidateForRoleError(RuntimeError):
    """指定角色没有任何可用候选."""

    def __init__(self, role: ModelType):
        super().__init__(f"角色 '{role.value}' 未配置任何候选模型")
        self.role = role


class RoleResolver:
    """按角色解析候选模型列表. 线程/协程安全."""

    def __init__(self, store: LLMServiceStore):
        self._store = store
        self._cache: dict[ModelType, list[ResolvedCandidate]] = {}
        self._lock = asyncio.Lock()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    async def for_role(
        self, role: ModelType, *, require: bool = True
    ) -> list[ResolvedCandidate]:
        """返回该角色的候选列表 (已按优先级升序).

        - ``require=True`` (默认) 空列表抛 ``NoCandidateForRoleError``.
        - ``require=False`` 允许返回空列表 (供调用方自己降级处理).
        """
        cached = self._cache.get(role)
        if cached is not None:
            candidates = cached
        else:
            async with self._lock:
                cached = self._cache.get(role)
                if cached is None:
                    cached = await self._store.resolve_role(role)
                    self._cache[role] = cached
                candidates = cached
        if require and not candidates:
            raise NoCandidateForRoleError(role)
        return candidates

    async def first(self, role: ModelType) -> ResolvedCandidate:
        """返回该角色最高优先级的候选. 空则抛 NoCandidateForRoleError."""
        candidates = await self.for_role(role, require=True)
        return candidates[0]

    def invalidate(self, role: Optional[ModelType] = None) -> None:
        """使缓存失效. role 省略时清空全部."""
        if role is None:
            self._cache.clear()
        else:
            self._cache.pop(role, None)
        self._version += 1
