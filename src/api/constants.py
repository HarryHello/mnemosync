"""API 全局常量."""

# 访问 Token 过期时间 (秒).
# 与 src/persistence/auth_store.py 中 SessionToken.generate 的默认 expires_hours=24 保持一致.
ACCESS_TOKEN_EXPIRE_SECONDS = 86400
