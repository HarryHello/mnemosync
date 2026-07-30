"""跨平台身份绑定内部 tool.

流程:
  1. 用户在一端请求绑定 -> 模型调用 initiate_identity_binding
     -> 生成 6 位验证码, 回复包含验证码
  2. 用户在另一端输入验证码 -> 模型调用 confirm_identity_binding(code)
     -> 校验, 绑定到同一 UserGroup

绑定码存储: 内存 dict + TTL (5 分钟过期)
绑定逻辑: 复用 SqliteIdentityStore 的 UserGroup
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from src.core.tools.internal_registry import (
    InternalTool,
    InternalToolRegistry,
)
from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)

# 绑定码 TTL (秒)
BINDING_CODE_TTL = 300  # 5 分钟
# 绑定码长度
BINDING_CODE_LENGTH = 6


class BindingCodeStore:
    """内存绑定码存储, 带 TTL."""

    def __init__(self) -> None:
        self._codes: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def generate(
        self,
        *,
        actor_id: str,
        space_id: str | None,
        display_name: str | None,
    ) -> str:
        """生成绑定码, 关联到当前 actor."""
        code = "".join(str(random.randint(0, 9)) for _ in range(BINDING_CODE_LENGTH))
        async with self._lock:
            self._codes[code] = {
                "actor_id": actor_id,
                "space_id": space_id,
                "display_name": display_name,
                "created_at": time.time(),
            }
            # 清理过期码
            self._cleanup()
        logger.info("生成绑定码: actor=%s, code=%s", actor_id, code)
        return code

    async def verify(self, code: str) -> dict[str, Any] | None:
        """校验绑定码, 返回关联信息; 无效或过期返回 None."""
        async with self._lock:
            self._cleanup()
            entry = self._codes.pop(code, None)
        if entry is None:
            return None
        return entry

    def _cleanup(self) -> None:
        """清理过期绑定码 (调用者需持锁)."""
        now = time.time()
        expired = [c for c, e in self._codes.items() if now - e["created_at"] > BINDING_CODE_TTL]
        for c in expired:
            del self._codes[c]
        if expired:
            logger.debug("清理 %d 个过期绑定码", len(expired))


# 全局单例
_code_store: BindingCodeStore | None = None


def get_binding_code_store() -> BindingCodeStore:
    global _code_store
    if _code_store is None:
        _code_store = BindingCodeStore()
    return _code_store


async def _handle_initiate_binding(
    *,
    actor_id: str | None,
    space_id: str | None,
    display_name: str | None,
    identity_store: SqliteIdentityStore,
    **_kwargs: Any,
) -> dict[str, Any]:
    """处理 initiate_identity_binding 调用."""
    if not actor_id:
        return {"success": False, "error": "当前用户未识别, 无法发起绑定"}

    store = get_binding_code_store()
    code = await store.generate(
        actor_id=actor_id, space_id=space_id, display_name=display_name,
    )
    return {
        "success": True,
        "code": code,
        "instruction": f"绑定码已生成: {code}。请让用户在另一端发送此验证码完成绑定。",
    }


async def _handle_confirm_binding(
    *,
    code: str,
    actor_id: str | None,
    identity_store: SqliteIdentityStore,
    **_kwargs: Any,
) -> dict[str, Any]:
    """处理 confirm_identity_binding 调用."""
    if not actor_id:
        return {"success": False, "error": "当前用户未识别, 无法确认绑定"}

    store = get_binding_code_store()
    entry = await store.verify(code)
    if entry is None:
        return {"success": False, "error": "验证码无效或已过期"}

    target_actor_id = entry["actor_id"]
    if target_actor_id == actor_id:
        return {"success": False, "error": "不能与自身绑定"}

    # 查询双方已有的 UserGroup
    target_groups = await identity_store.list_actor_groups(target_actor_id)
    current_groups = await identity_store.list_actor_groups(actor_id)

    if current_groups:
        # 当前用户已在某个组 -> 拒绝 (已绑定)
        return {
            "success": False,
            "error": "当前账号已绑定到用户组, 请先解绑再重新绑定",
        }

    if target_groups:
        # 目标用户有组 -> 当前用户加入目标用户的组
        group_id = target_groups[0].id
        await identity_store.bind_actor_to_group(actor_id, group_id)
        return {
            "success": True,
            "group_id": group_id,
            "message": f"已绑定到用户组 {group_id}",
        }
    else:
        # 双方都没有组 -> 创建新组, 两人都加入
        group = await identity_store.create_group(name=None)
        await identity_store.bind_actor_to_group(target_actor_id, group.id)
        await identity_store.bind_actor_to_group(actor_id, group.id)
        return {
            "success": True,
            "group_id": group.id,
            "message": f"已创建新用户组 {group.id} 并绑定双方",
        }


def register_identity_binding_tools(registry: InternalToolRegistry) -> None:
    """注册身份绑定相关内部 tool."""
    registry.register(InternalTool(
        name="initiate_identity_binding",
        description=(
            "发起跨平台身份绑定。当用户表达了在不同平台是同一个人的意愿时调用。"
            "生成一个 6 位验证码, 用户需要在另一端提供此码完成绑定。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_initiate_binding,
    ))
    registry.register(InternalTool(
        name="confirm_identity_binding",
        description=(
            "确认跨平台身份绑定。用户提供了验证码时调用。"
            "验证通过后, 当前账号将与发起绑定的账号绑定到同一用户组。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "6 位数字验证码",
                },
            },
            "required": ["code"],
        },
        handler=_handle_confirm_binding,
    ))
