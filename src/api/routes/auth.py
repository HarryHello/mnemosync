"""认证路由."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.constants import ACCESS_TOKEN_EXPIRE_SECONDS
from src.api.deps import get_auth_store
from src.persistence.auth_store import SqliteAuthStore, User

from ..schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    SetupCredentialsRequest,
    SetupCredentialsResponse,
    UserInfo,
    UserInfoResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    """获取当前登录用户.

    先从 credentials 判 401, 只有确定要查库时才取 auth_store, 便于测试用
    dependency_overrides 直接短路. 生产环境走 lifespan 已注入的单例长连接.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_store: SqliteAuthStore = get_auth_store(request)
    token = credentials.credentials

    try:
        session = await auth_store.get_session(token)
        user = await auth_store.get_user_by_id(session.user_id)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在或已禁用",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("认证失败: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_password_settled(
    current_user: User = Depends(get_current_user),
) -> User:
    """拦截首次登录未改凭证的用户.

    在非 auth 路由 include 时统一注入; must_change_password=True 一律 403,
    强制走 /panel/auth/setup-credentials 完成首次设置. 前端守卫是引导,
    服务端此拦截才是硬保证 (即便 UI 被绕过).
    """
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required",
        )
    return current_user


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="用户登录",
    description="使用用户名和密码登录，默认账号密码都是 mnemosync",
)
async def login(
    request: LoginRequest,
    auth_store: SqliteAuthStore = Depends(get_auth_store),
) -> LoginResponse:
    """用户登录."""
    # 检查是否需要创建默认用户
    existing_user = await auth_store.get_user_by_username(request.username)
    if not existing_user:
        try:
            if request.username == "mnemosync" and request.password == "mnemosync":
                await auth_store.create_default_user(request.password)
            else:
                raise ValueError("用户名或密码错误")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

    try:
        user = await auth_store.authenticate(request.username, request.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    session = await auth_store.create_session(user.id)

    return LoginResponse(
        access_token=session.raw_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_SECONDS,
        must_change_password=user.must_change_password,
        username=user.username,
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="用户登出",
    description="使当前 Token 失效",
)
async def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    auth_store: SqliteAuthStore = Depends(get_auth_store),
) -> MessageResponse:
    """用户登出."""
    if credentials:
        await auth_store.invalidate_session(credentials.credentials)

    return MessageResponse(success=True, message="已登出")


@router.get(
    "/me",
    response_model=UserInfoResponse,
    summary="获取当前用户信息",
    description="获取当前登录用户的详细信息",
)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> UserInfoResponse:
    """获取当前用户信息."""
    return UserInfoResponse(
        user=UserInfo(
            id=current_user.id,
            username=current_user.username,
            must_change_password=current_user.must_change_password,
            created_at=current_user.created_at.isoformat(),
            last_login_at=(
                current_user.last_login_at.isoformat()
                if current_user.last_login_at
                else None
            ),
        )
    )


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    summary="修改密码",
    description="修改当前用户的密码; 若 must_change_password=True, 请改用 /auth/setup-credentials",
)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    auth_store: SqliteAuthStore = Depends(get_auth_store),
) -> ChangePasswordResponse:
    """修改密码."""
    try:
        await auth_store.change_password(
            current_user.id,
            request.old_password,
            request.new_password,
        )
        return ChangePasswordResponse(success=True, message="密码已修改，请重新登录")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/setup-credentials",
    response_model=SetupCredentialsResponse,
    summary="首次登录设置账号密码",
    description="仅当 must_change_password=True 时可用; 一次性设定新用户名与新密码, 完成后强制重新登录",
)
async def setup_credentials(
    request: SetupCredentialsRequest,
    current_user: User = Depends(get_current_user),
    auth_store: SqliteAuthStore = Depends(get_auth_store),
) -> SetupCredentialsResponse:
    """首次登录设置账号密码 (用户名 + 密码同时改)."""
    if not current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="账号已完成初始化, 请使用修改密码接口",
        )
    try:
        await auth_store.change_username_and_password(
            current_user.id,
            request.old_password,
            request.new_username,
            request.new_password,
        )
        return SetupCredentialsResponse(
            success=True, message="账号密码已设置, 请重新登录"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
