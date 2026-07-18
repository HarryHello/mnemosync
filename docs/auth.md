# 认证 API 文档

> **系统版本**: v0.2.6
> **文档状态**: 与代码同步
> **最后更新**: 2026-07-18

---

## 1. 概述

Mnemosync 采用 用户名 + 密码 的认证方式管理**管理员**账号, 首次部署自动创建默认账号 `mnemosync / mnemosync`。

> 此处的"用户"指**管理员**——用于登录 WebUI/CLI 管理 Mnemosync 本身。与前端接入使用的 API Key、以及各前端自己的最终用户是三个独立层次。参见 [概念区分](#5-概念区分)。

**路由前缀**: `/auth` (真实定义见 [src/api/routes/auth.py:25](../src/api/routes/auth.py#L25))。

**数据库**: `data/auth.db`, 存储用户和会话 Token。

---

## 2. 快速开始

### 2.1 初始化

首次部署运行:

```bash
mnemosync init                # 本地模式
mnemosync init --docker       # Docker 模式
```

`init` 会初始化 `data/auth.db` 及其它数据库文件。默认管理员在首次成功登录 `mnemosync/mnemosync` 时自动创建 (见 [login 逻辑](../src/api/routes/auth.py#L83))。

### 2.2 登录

```bash
curl -X POST http://localhost:16125/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "mnemosync", "password": "mnemosync"}'
```

响应:

```json
{
  "access_token": "abc123...",
  "token_type": "bearer",
  "expires_in": 86400,
  "must_change_password": true,
  "username": "mnemosync"
}
```

### 2.3 修改密码 (首次强制)

```bash
curl -X POST http://localhost:16125/auth/change-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "mnemosync", "new_password": "your_new_password"}'
```

### 2.4 访问受保护端点

```bash
curl http://localhost:16125/auth/me \
  -H "Authorization: Bearer <token>"
```

---

## 3. API 端点

所有端点前缀 `/auth`。

### POST /auth/login

**请求**:
```json
{ "username": "string (1-50)", "password": "string (1-128)" }
```

**响应** (200):
```json
{
  "access_token": "string",
  "token_type": "bearer",
  "expires_in": 86400,
  "must_change_password": true,
  "username": "string"
}
```

**错误**: `401` 用户名或密码错误。

### POST /auth/logout

**请求头**: `Authorization: Bearer <token>`

**响应** (200): `{ "success": true, "message": "已登出" }`

### GET /auth/me

**请求头**: `Authorization: Bearer <token>`

**响应** (200):
```json
{
  "user": {
    "id": "string",
    "username": "string",
    "must_change_password": true,
    "created_at": "2026-07-01T10:00:00+00:00",
    "last_login_at": "2026-07-15T12:00:00+00:00"
  }
}
```

### POST /auth/change-password

**请求头**: `Authorization: Bearer <token>`

**请求**:
```json
{ "old_password": "string", "new_password": "string (最少 6 字符)" }
```

**响应** (200): `{ "success": true, "message": "密码已修改，请重新登录" }`

**错误**: `400` 旧密码错误或新密码不合法。

### POST /auth/init-default-user

无鉴权; 幂等创建默认管理员 `mnemosync/mnemosync`。用于外部工具首次初始化。

---

## 4. CLI 命令

顶层命令 (真实定义见 [src/cli/cli.py](../src/cli/cli.py)):

| 命令 | 说明 |
|------|------|
| `mnemosync init [--docker]` | 初始化数据库 |
| `mnemosync serve` | 启动服务 |
| `mnemosync stop` | 停止服务 (Docker 模式) |
| `mnemosync login` | 进入交互式 CLI |
| `mnemosync ask <msg>` | 命令行直连主对话 (调试) |
| `mnemosync upgrade` | 升级 Mnemosync |
| `mnemosync help` | 帮助 |

**API Key / 用户管理不在顶层命令**——通过 `mnemosync login` 进入交互式 shell 后使用: `generate-key`, `list-keys`, `revoke-key`, `list-users`, `change-password` 等。见 [CLI 文档](modules/cli.md)。

---

## 5. 概念区分

| 概念 | 说明 | 用途 |
|------|------|------|
| **管理员用户** | 登录 WebUI/CLI 的账号 | 配置人格、管理 API Key、查看日志 |
| **API Key** | 前端客户端使用的密钥 | AstrBot/AIRI/Web 等接入 `/v1/chat/completions` |
| **最终用户** | 与前端对话的普通人 | 由各前端自行管理, Mnemosync 通过 `source_user` 字段区分 |

```
管理员
   │登录
   ▼
Mnemosync 管理面 (auth + api-keys + admin)
   │生成
   ▼
API Key sk-xxx
   │分发给前端
   ▼
AstrBot / AIRI / Web  ── 走 /v1/chat/completions
   │
   ▼
最终用户 (QQ 好友 / 网站访客 …)
```

---

## 6. 安全建议

1. 首次登录立即修改默认密码
2. 生产环境启用 HTTPS
3. Token 有效期 86400s (24h); 手动 `POST /auth/logout` 提前失效
4. 定期备份 `data/auth.db` 与 `data/api_keys.db`
5. API Key 泄露后立即在管理面吊销

---

## 7. Admin 接口鉴权

`/panel/admin/*` 下的所有路由 (health / logs / memories / relationship / prompts) 从 v0.2.1 (2026-07-16) 起统一走 `Depends(get_current_user)` — **未携带有效 Bearer token 或 token 过期一律返回 401**。这是**路由级前置**, 在 [`src/api/routes/admin.py`](../src/api/routes/admin.py) 的 `APIRouter(..., dependencies=[Depends(get_current_user)])` 声明, 而非单接口装饰。

**背景**: 此前 admin 面板的 health/logs/memories/relationship 均无鉴权 (裸奔), 引入 prompt 写接口时**一并**堵上。凡新增到 admin router 的路由自动继承鉴权, 不能在单路由上"跳过"。

**使用**: 与 [`/auth/me`](#get-authme) 相同, 携带 `Authorization: Bearer <token>` 请求头即可。Token 从 `POST /auth/login` 获取。

---

## 8. 数据库

| 文件 | 用途 |
|------|------|
| `data/auth.db` | 管理员账号 + 会话 Token |
| `data/api_keys.db` | 前端 API Key (含 v0.2.5 `source` 列) |
| `data/conversation.db` | v0.2.6 跨前端短期记忆 (`conversation_turns`) |
| `data/http_logs.db` | v0.2.5 调试面板 HTTP 日志 |

---

## 9. 常见问题

**Q: 支持多管理员吗?**
A: 数据模型支持, 但当前 UI/CLI 只暴露默认管理员; 需要手动通过 auth_store 添加。

**Q: 未来会支持最终用户系统吗?**
A: 单人格架构下不打算支持——终端用户由前端自行管理。多人格是未来规划 (v1.0+)。

**Q: API Key 与代理思考的关系?**
A: 独立。API Key 只做鉴权; 代理思考是否启用由 [`src/api/reasoning_control.py`](../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 按 4 条规则决策 (tools 存在 → 关; 原生思考模型 → 关; 前台点名 `reasoning_effort` / `thinking` / `reasoning` → 开; 否则回落 `[graph].proxy_thinking_default`)。详见 [agents.md](modules/agents.md) §4。

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 初始认证系统 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 路由前缀 `/auth` (非 `/api/v1/auth`), CLI 顶层命令列表修正, 代理思考启用方式修正 |
| v0.2.3 | 2026-07-17 | 面板端口前缀由 `/api/v1` 改为 `/panel`, 与 OpenAI 兼容层 `/v1` 完全隔离 |
| v0.2.4 | 2026-07-17 | 新增 `/panel/admin/model-bindings/probe-dimension` + `/panel/admin/memory/reindex` + `/panel/admin/memory/reindex/status` + `/panel/admin/memory/prune`, 全部通过 admin router 的 `Depends(get_current_user)` 前置鉴权 |
| v0.2.5 | 2026-07-18 | 新增调试面板路由 `/panel/admin/debug/*` (session-key / status / events / events/{id} / events/stream (SSE) / DELETE events), 均前置鉴权; `api_keys` 表新增 `source` 列 (`user` / `panel-debug`), `/panel/api-keys` 只列出 `source=user`, 调试面板自动生成的 key 不可通过用户 API 撤销 |
| v0.2.6 | 2026-07-18 | 与代码对齐: 代理思考启用方式修正 (`reasoning_control.should_use_proxy_thinking` 4 条规则); 数据库表新增 `data/conversation.db` (跨前端短期记忆) 与 `data/http_logs.db` (v0.2.5 调试面板日志) |
