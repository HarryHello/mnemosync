"""管理 API 路由聚合器.

将各子域路由组合到统一的 /panel/admin 前缀下. 实际路由定义分散在:

- admin_core.py        — 健康检查、仪表盘、HTTP 日志
- admin_memories.py    — 记忆 CRUD、关系、reindex、prune
- admin_prompts.py     — Agent 提示词覆盖管理
- admin_upstream.py    — 上游 LLM 服务商 + 模型绑定
- admin_conversation.py — 跨前端对话流水管理
- admin_notifications.py — 通知中心
- admin_identity.py    — 身份管理 (Actors / UserGroups / Strategies)
- admin_persona.py     — 人格配置与重置

**认证**: 所有路由要求登录 (Depends(get_current_user)), 由父 router 统一注入.
"""

from fastapi import APIRouter, Depends

from src.api.routes.admin_agent_runs import router as agent_runs_router
from src.api.routes.admin_conversation import router as conversation_router
from src.api.routes.admin_core import router as core_router
from src.api.routes.admin_identity import router as identity_router
from src.api.routes.admin_memories import router as memories_router
from src.api.routes.admin_notifications import router as notifications_router
from src.api.routes.admin_persona import router as persona_router
from src.api.routes.admin_prompts import router as prompts_router
from src.api.routes.admin_restart import router as restart_router
from src.api.routes.admin_upstream import router as upstream_router
from src.api.routes.auth import get_current_user

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)

# 按域挂载子路由 — 顺序不影响功能, 仅为可读性排列
router.include_router(core_router)
router.include_router(memories_router)
router.include_router(prompts_router)
router.include_router(upstream_router)
router.include_router(conversation_router)
router.include_router(notifications_router)
router.include_router(identity_router)
router.include_router(persona_router)
router.include_router(agent_runs_router)
router.include_router(restart_router)
