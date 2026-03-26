"""API 层."""

from fastapi import APIRouter

from .routes.api_key import router as api_key_router
from .routes.auth import router as auth_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(api_key_router)

__all__ = [
    "api_router",
    "api_key_router",
    "auth_router",
]
