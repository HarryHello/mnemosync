# 认证 API 文档

> **最后更新**: 2026-03-29

---

## 概述

Mnemosync 采用帐号密码认证方式，默认账号密码都是 `mnemosync`。

> ⚠️ **重要说明**：
> 
> 此处的"用户"指的是**管理员用户**，用于登录 WebUI 或 CLI 来管理 Mnemosync 服务。
> 
> **不是**多用户系统中的"最终用户"概念。当前版本为单人格架构，所有前端客户端共享同一人格配置。

---

## 快速开始

### 1. 初始化服务器（含默认管理员）

首次部署时，使用 `init` 命令初始化服务器，会自动创建默认管理员：

```bash
# 使用 CLI 初始化（Docker 部署）
mnemosync init
```

输出示例：
```
Mnemosync initializing...
Building Docker image...
Initializing database...
Success!

Use `mnemosync login` to start the cli environment,
or use `mnemosync help` to get more information.
```

> **说明**：`mnemosync init` 会构建 Docker 镜像并初始化数据库，默认管理员帐号（用户名/密码：`mnemosync`）会在初始化时自动创建。

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

---

## API 接口

### POST /api/v1/auth/login

管理员登录

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

管理员登出

**请求头：**
```
Authorization: Bearer <token>
```

### GET /api/v1/auth/me

获取当前管理员信息

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

初始化默认管理员（仅首次）

**响应：**
```json
{
  "success": true,
  "message": "默认管理员已创建，用户名和密码都是 mnemosync，首次登录请修改密码"
}
```

---

## CLI 命令

```bash
# 初始化服务器（含默认管理员）
mnemosync init

# 列出所有管理员
mnemosync list-users

# 修改管理员密码
mnemosync change-password <username> <old_password> <new_password>

# 生成 API Key（用于前端客户端）
mnemosync generate "备注"

# 列出 API Key
mnemosync list

# 撤销 API Key
mnemosync revoke <key_id>
```

---

## 概念区分

| 概念 | 说明 | 用途 |
|------|------|------|
| **管理员用户** | 登录 WebUI/CLI 的帐号 | 配置人格、管理 API Key、查看日志 |
| **API Key** | 前端客户端使用的密钥 | AstrBot/AIRI/Web 等前端接入时使用 |
| **最终用户** | 与前端对话的普通人 | 如 QQ 好友、网站访客，不由 Mnemosync 管理 |

```
┌─────────────────────────────────────────────────────────────┐
│  管理员用户                                                  │
│  帐号：mnemosync / 密码：*****                               │
│         ↓ 登录                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Mnemosync WebUI / CLI                               │    │
│  │  • 配置人格设定                                       │    │
│  │  • 生成/管理 API Key                                 │    │
│  │  • 查看对话日志                                       │    │
│  └─────────────────────────────────────────────────────┘    │
│         ↓ 生成 API Key                                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  API Key: sk-abc12345                                │    │
│  └─────────────────────────────────────────────────────┘    │
│         ↓ 配置到前端                                          │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │  AstrBot      │  │  AIRI 桌宠    │  │  Web 聊天室   │   │
│  │  Key: sk-...  │  │  Key: sk-...  │  │  Key: sk-...  │   │
│  └───────────────┘  └───────────────┘  └───────────────┘   │
│         ↓                  ↓                  ↓             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  最终用户 (QQ 好友 / 网站访客等)                       │    │
│  │  与前端对话，不直接接触 Mnemosync                     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 安全建议

1. **首次登录后立即修改密码**
2. **定期更换密码**
3. **不要使用弱密码**
4. **妥善保管 API Key**（泄露后撤销并重新生成）
5. **生产环境使用 HTTPS**
6. **定期备份数据库文件**

---

## 数据库文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 认证数据库 | `data/auth.db` | 存储管理员用户和会话 Token |
| API Key 数据库 | `data/api_keys.db` | 存储 API Key（前端客户端用） |

---

## 常见问题

### Q: 支持多用户注册吗？
A: 当前版本不支持。管理员帐号用于管理服务，最终用户由各自前端管理。

### Q: 可以为不同用户生成不同的 API Key 吗？
A: 可以生成多个 API Key，但它们共享同一人格配置。API Key 用于区分前端来源，而非用户隔离。

### Q: 未来会支持多用户吗？
A: 多人格/多用户支持是未来规划（v1.0+），当前专注于单人格场景的完善。
