"""API 层."""

from fastapi import APIRouter

from .routes.api_key import router as api_key_router
from .routes.auth import router as auth_router
from .routes.forward import router as forward_router

# 内部 API 路由 (Mnemosync 自身管理用)
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(api_key_router)

# OpenAI 兼容的路由 (对外服务用)
# 这些路由直接挂载到根路径，不使用 /api 前缀
__all__ = [
    "api_router",
    "forward_router",
    "api_key_router",
    "auth_router",
]
