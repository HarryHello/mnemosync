"""管理 API 路由 - 身份管理 (Actors / UserGroups / IdentityStrategies).

提供身份识别策略 CRUD、AI 辅助配置生成、Actor/Group 管理及绑定接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).

模块拆分:
  * identity_strategies.py — 身份策略 CRUD + AI 配置生成
  * identity_actors.py     — 参与者管理 + Actor ↔ Group 绑定
  * identity_groups.py     — 用户组管理
  * identity_plugins.py    — 插件管理 (发现/安装/卸载/代理)
"""

from fastapi import APIRouter

from src.api.routes.admin_identity.identity_actors import router as actors_router
from src.api.routes.admin_identity.identity_groups import router as groups_router
from src.api.routes.admin_identity.identity_plugins import router as plugins_router
from src.api.routes.admin_identity.identity_strategies import router as strategies_router

router = APIRouter()

# 按域挂载子路由 — 顺序不影响功能, 仅为可读性排列
router.include_router(strategies_router)
router.include_router(actors_router)
router.include_router(groups_router)
router.include_router(plugins_router)
