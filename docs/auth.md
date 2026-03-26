# 认证 API 文档

## 概述

Mnemosync 采用帐号密码认证方式，默认账号密码都是 `mnemosync`。

## 快速开始

### 1. 初始化默认用户

首次部署时，需要初始化默认用户：

```bash
# 使用 CLI 初始化
mnemosync init-user

# 或调用 API
curl -X POST http://localhost:16125/api/v1/auth/init-default-user
```

### 2. 登录获取 Token

```bash
curl -X POST http://localhost:16125/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "mnemosync", "password": "mnemosync"}'
```

响应：
```json
{
  "access_token": "abc123...",
  "token_type": "bearer",
  "expires_in": 86400,
  "must_change_password": true,
  "username": "mnemosync"
}
```

### 3. 首次登录修改密码

```bash
curl -X POST http://localhost:16125/api/v1/auth/change-password \
  -H "Authorization: Bearer <your_token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "mnemosync", "new_password": "your_new_password"}'
```

### 4. 使用 Token 访问受保护接口

```bash
curl http://localhost:16125/api/v1/auth/me \
  -H "Authorization: Bearer <your_token>"
```

## API 接口

### POST /api/v1/auth/login

用户登录

**请求体：**
```json
{
  "username": "string (1-50 字符)",
  "password": "string (1-128 字符)"
}
```

**响应：**
```json
{
  "access_token": "string",
  "token_type": "bearer",
  "expires_in": 86400,
  "must_change_password": true,
  "username": "string"
}
```

### POST /api/v1/auth/logout

用户登出

**请求头：**
```
Authorization: Bearer <token>
```

### GET /api/v1/auth/me

获取当前用户信息

**请求头：**
```
Authorization: Bearer <token>
```

**响应：**
```json
{
  "user": {
    "id": "string",
    "username": "string",
    "must_change_password": true,
    "created_at": "2026-03-25T10:00:00+00:00",
    "last_login_at": "2026-03-25T12:00:00+00:00"
  }
}
```

### POST /api/v1/auth/change-password

修改密码

**请求头：**
```
Authorization: Bearer <token>
```

**请求体：**
```json
{
  "old_password": "string",
  "new_password": "string (最少 6 字符)"
}
```

**响应：**
```json
{
  "success": true,
  "message": "密码已修改，请重新登录"
}
```

### POST /api/v1/auth/init-default-user

初始化默认用户（仅首次）

**响应：**
```json
{
  "success": true,
  "message": "默认用户已创建，用户名和密码都是 mnemosync，首次登录请修改密码"
}
```

## CLI 命令

```bash
# 初始化默认用户
mnemosync init-user

# 列出所有用户
mnemosync list-users

# 修改用户密码
mnemosync change-password <username> <old_password> <new_password>

# 生成 API Key
mnemosync generate "备注"

# 列出 API Key
mnemosync list

# 撤销 API Key
mnemosync revoke <key_id>
```

## 安全建议

1. **首次登录后立即修改密码**
2. **定期更换密码**
3. **不要使用弱密码**
4. **妥善保管 API Key**
5. **生产环境使用 HTTPS**
6. **定期备份数据库文件**

## 数据库文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 认证数据库 | `data/auth.db` | 存储用户和会话 |
| API Key 数据库 | `data/api_keys.db` | 存储 API Key |
