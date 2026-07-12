"""认证路由."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.persistence.auth_store import (
    SqliteAuthStore,
    User,
)

from ..schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    UserInfo,
    UserInfoResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

DB_PATH = "data/auth.db"


def _get_auth_store() -> SqliteAuthStore:
    """获取认证存储实例."""
    return SqliteAuthStore(DB_PATH)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    auth_store: SqliteAuthStore = Depends(_get_auth_store),
) -> User:
    """获取当前登录用户."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

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


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="用户登录",
    description="使用用户名和密码登录，默认账号密码都是 mnemosync",
)
async def login(
    request: LoginRequest,
    auth_store: SqliteAuthStore = Depends(_get_auth_store),
) -> LoginResponse:
    """用户登录."""
    await auth_store.init_db()

    # 检查是否需要创建默认用户
    existing_user = await auth_store.get_user_by_username(request.username)
    if not existing_user:
        # 尝试创建默认用户
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

    # 创建会话
    session = await auth_store.create_session(user.id)

    return LoginResponse(
        access_token=session.raw_token,
        token_type="bearer",
        expires_in=86400,
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
    auth_store: SqliteAuthStore = Depends(_get_auth_store),
) -> MessageResponse:
    """用户登出."""
    await auth_store.init_db()

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
    description="修改当前用户的密码，首次登录必须修改",
)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    auth_store: SqliteAuthStore = Depends(_get_auth_store),
) -> ChangePasswordResponse:
    """修改密码."""
    await auth_store.init_db()

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
    "/init-default-user",
    response_model=MessageResponse,
    summary="初始化默认用户",
    description="创建默认管理员用户 (用户名和密码都是 mnemosync)",
)
async def init_default_user(
    auth_store: SqliteAuthStore = Depends(_get_auth_store),
) -> MessageResponse:
    """初始化默认用户."""
    await auth_store.init_db()

    try:
        await auth_store.create_default_user("mnemosync")
        return MessageResponse(
            success=True,
            message="默认用户已创建，用户名和密码都是 mnemosync，首次登录请修改密码",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"创建失败：{str(e)}",
        )
