"""管理 API 路由 - 人格配置与重置.

提供人格状态重置 (清空记忆/关系/流水/向量库) 和人格配置覆盖编辑接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).

模块拆分:
  * persona_crud.py        — 人格 CRUD + 配置 (config / definition / profiles)
  * persona_reset.py       — 人格重置 (persona/reset)
  * persona_import.py      — 角色卡导入导出 (import-card / export)
  * persona_versions.py    — 人格版本管理
  * persona_space_policy.py — 空间社交策略
  * models.py              — 共享 Pydantic 模型
  * _helpers.py            — 共享辅助函数
"""

from fastapi import APIRouter

from src.api.routes.admin_persona.persona_crud import router as crud_router
from src.api.routes.admin_persona.persona_import import router as import_router
from src.api.routes.admin_persona.persona_reset import router as reset_router
from src.api.routes.admin_persona.persona_space_policy import router as space_policy_router
from src.api.routes.admin_persona.persona_versions import router as versions_router

router = APIRouter()

# 按域挂载子路由 — 顺序不影响功能, 仅为可读性排列
router.include_router(crud_router)
router.include_router(reset_router)
router.include_router(import_router)
router.include_router(versions_router)
router.include_router(space_policy_router)
