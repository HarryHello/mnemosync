"""认证相关的 Pydantic 模式."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """登录请求."""

    username: str = Field(..., min_length=1, max_length=50, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


class LoginResponse(BaseModel):
    """登录响应."""

    access_token: str = Field(..., description="访问 Token")
    token_type: str = Field(default="bearer", description="Token 类型")
    expires_in: int = Field(default=86400, description="过期时间 (秒)")
    must_change_password: bool = Field(..., description="是否必须修改密码")
    username: str = Field(..., description="用户名")


class ChangePasswordRequest(BaseModel):
    """修改密码请求."""

    old_password: str = Field(..., min_length=1, max_length=128, description="原密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码")


class ChangePasswordResponse(BaseModel):
    """修改密码响应."""

    success: bool = Field(default=True, description="是否成功")
    message: str = Field(default="密码已修改", description="消息")


class UserInfo(BaseModel):
    """用户信息."""

    id: str = Field(..., description="用户 ID")
    username: str = Field(..., description="用户名")
    must_change_password: bool = Field(..., description="是否必须修改密码")
    created_at: str = Field(..., description="创建时间")
    last_login_at: str | None = Field(None, description="最后登录时间")


class UserInfoResponse(BaseModel):
    """用户信息响应."""

    user: UserInfo = Field(..., description="用户信息")


class LogoutRequest(BaseModel):
    """登出请求."""

    token: str | None = Field(None, description="要撤销的 Token (不传则撤销当前 Token)")


class MessageResponse(BaseModel):
    """通用消息响应."""

    success: bool = Field(default=True, description="是否成功")
    message: str = Field(..., description="消息")
