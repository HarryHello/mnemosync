"""API 层."""

from fastapi import APIRouter, Depends

from .routes.admin import router as admin_router
from .routes.admin_debug import router as admin_debug_router
from .routes.api_key import router as api_key_router
from .routes.auth import require_password_settled, router as auth_router
from .routes.forward import router as forward_router

# 面板/内部管理路由 (Mnemosync 自身管理用, 不对外)
# 与 OpenAI 兼容层 (/v1/*) 完全分开, 避免第三方客户端与反向代理误伤
# auth 路由不注入 require_password_settled — /login /setup-credentials /me /logout
# 是白名单; 其它面板路由统一硬拦, 首次登录未改凭证 → 403.
api_router = APIRouter(prefix="/panel")
api_router.include_router(auth_router)
_settled = [Depends(require_password_settled)]
api_router.include_router(api_key_router, dependencies=_settled)
api_router.include_router(admin_router, dependencies=_settled)
api_router.include_router(admin_debug_router, dependencies=_settled)

# OpenAI 兼容路由 (/v1/*) 对外, 直接挂载到根路径
__all__ = [
    "api_router",
    "forward_router",
    "api_key_router",
    "auth_router",
    "admin_router",
    "admin_debug_router",
]
