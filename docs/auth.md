# 认证 API 文档

> **系统版本**: v0.3.4
> **文档状态**: 与代码同步
> **最后更新**: 2026-08-01

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

### 2.3 首次登录设置账号密码 (强制)

首次登录后, `LoginResponse.must_change_password=true`; 此时面板会自动跳转 `/setup` 页面, 且**服务端硬拦**: 除 `/panel/auth/*` 白名单外, 所有 `/panel/*` 返回 `403 password_change_required` (v0.2.12)。

```bash
curl -X POST http://localhost:16125/panel/auth/setup-credentials \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "mnemosync", "new_username": "harry", "new_password": "your_new_password"}'
```

一次性同时改用户名与密码, 完成后 `must_change_password=false`, 需重新登录。之后再调此端点将返回 400, 改用 `/auth/change-password`。

### 2.4 修改密码 (日常)

```bash
curl -X POST http://localhost:16125/auth/change-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "mnemosync", "new_password": "your_new_password"}'
```

### 2.5 访问受保护端点

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

> 若当前用户 `must_change_password=true`, 应改用 `/auth/setup-credentials` (可同时改用户名), 而非此端点。

### POST /auth/setup-credentials

首次登录一次性设定新用户名与新密码 (v0.2.12)。仅当当前用户 `must_change_password=true` 时可用。

**请求头**: `Authorization: Bearer <token>`

**请求**:
```json
{
  "old_password": "string",
  "new_username": "string (1-50)",
  "new_password": "string (最少 6 字符, 不能是默认密码)"
}
```

**响应** (200): `{ "success": true, "message": "账号密码已设置, 请重新登录" }`

**错误**:
- `400 账号已完成初始化` — 当前 `must_change_password=false`, 请改用 `/auth/change-password`
- `400 用户名已被占用`
- `400 原密码错误`
- `400 密码强度不足` (< 6 位或等于默认密码 `mnemosync`)
- `422` — Pydantic 长度/类型校验失败

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
| **管理员用户** | 登录 WebUI/CLI 的账号 | 配置人格、管理 API Key、查看日志、管理身份 |
| **API Key** | 前端客户端使用的密钥 | AstrBot/AIRI/Web 等接入 `/v1/chat/completions`; v0.3.0 起可绑定身份策略 (`strategy_id`) |
| **参与者 (Actor)** | 前端上的一个可识别账号 (v0.3.0) | 由服务器按 API Key 绑定的策略从请求中解析, (frontend, external_key) 唯一 |
| **最终用户 (UserGroup)** | 与前端对话的普通人 (v0.3.0) | 一个真实人 = 一个用户组; 多平台 Actor 绑到同组后共享记忆与关系 (effective_user_id) |

```
管理员
   │登录
   ▼
Mnemosync 管理面 (auth + api-keys + identity + admin)
   │生成 API Key + 绑定身份策略
   ▼
API Key sk-xxx (strategy_id → direct/api_key_bound/regex/llm)
   │分发给前端
   ▼
AstrBot / AIRI / Web  ── 走 /v1/chat/completions
   │
   ▼
服务器侧身份解析 → Actor → effective_user_id (未绑策略 → 非归属模式)
```

身份解析流程、14 个 `/panel/admin/identity/*` 管理端点与 `mnemosync identity` CLI 详见 [modules/identity.md](modules/identity.md)。关系端点 (`GET/PUT /panel/admin/relationship` 等) 另支持 `actor_id` 查询参数, 自动解析为 effective_user_id。
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

### 7.1 首次登录硬拦 (v0.2.12)

面板端 (`/panel/api_router`) 除 `/panel/auth/*` 白名单外, 所有非 auth 路由在 include 时统一注入 `require_password_settled` dependency ([src/api/routes/auth.py](../src/api/routes/auth.py)) — 若当前用户 `must_change_password=true`, 返回 `403 password_change_required`, 不放行进业务处理。

**白名单** (不受此拦截):
- `POST /panel/auth/login`
- `POST /panel/auth/logout`
- `GET /panel/auth/me`
- `POST /panel/auth/change-password`
- `POST /panel/auth/setup-credentials`
- `POST /panel/auth/init-default-user`

**不受影响**: `/v1/*` OpenAI 兼容层走 API Key 鉴权, 与本拦截完全隔离 — 即便管理员首次登录未完成, 已生成的 API Key 仍可调用 `/v1/chat/completions`。

**前端**: `ui/src/router/index.ts` 全局守卫在 `must_change_password=true` 时强制跳 `/setup`; 但**服务端拦截才是权威**, F12 绕过 UI 亦无用。CLI `mnemosync login` 仍是修改用户名的唯一入口 (面板不做此功能)。

---

## 8. 数据库

| 文件 | 用途 |
|------|------|
| `data/auth.db` | 管理员账号 + 会话 Token |
| `data/api_keys.db` | 前端 API Key (含 v0.2.5 `source` 列、v0.3.0 `strategy_id` 列) |
| `data/conversation.db` | v0.2.6 跨前端短期记忆 (`conversation_turns`; v0.3.0 含空间事件流列) |
| `data/http_logs.db` | v0.2.5 调试面板 HTTP 日志 |
| `data/memory.db` | v0.2.10 长期记忆 + `relationship_audit_log` (字段级审计) |
| `data/notifications.db` | v0.2.13 通知中心 |
| `data/identity.db` | v0.3.0 身份四表 (actors / user_groups / actor_group_memberships / identity_strategies) |
| `data/idempotency.db` | v0.3.0 幂等重放缓存 (平台重发消息原样返回首次响应) |
| `data/persona.db` | v0.3.3 结构化人格版本存储 (personas + persona_versions) |
| `data/lorebook.db` | v0.3.3 Lorebook 关键词知识条目 |
| `data/space_policy.db` | v0.3.3 空间社交策略 |
| `data/persona_override.toml` | v0.2.11 面板 `PUT /panel/admin/persona` 落地的人格覆盖 (优先级最高), 见 [configuration.md §3.1](configuration.md#31-persona-v021-服务器优先人格) |

---

## 9. 常见问题

**Q: 支持多管理员吗?**
A: 数据模型支持, 但当前 UI/CLI 只暴露默认管理员; 需要手动通过 auth_store 添加。

**Q: 最终用户如何管理?**
A: v0.3.0 起 Mnemosync 内置单人格多用户身份体系: 服务器按 API Key 绑定的策略从请求中解析参与者 (Actor), 管理员可在面板「身份管理」页或 `mnemosync identity` CLI 中把同一人的多平台 Actor 绑定到用户组, 记忆与关系按 effective_user_id 隔离/共享。不依赖任何前端配合。详见 [modules/identity.md](modules/identity.md)。多人格仍是未来规划 (v1.0+)。

**Q: API Key 与代理思考的关系?**
A: 独立。API Key 只做鉴权与身份策略绑定; 代理思考是否启用由 [`src/api/reasoning_control.py`](../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 按 4 条规则决策 (tools 存在 → 关; 原生思考模型 → 关; 前台点名 `reasoning_effort` / `thinking` / `reasoning` → 开; 否则回落 `[graph].proxy_thinking_default`)。详见 [agents.md](modules/agents.md) §4。

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
| v0.2.7 | 2026-07-18 | 新增 `POST /panel/admin/persona/reset`: 原子清空 memory_entries (含 PERMANENT) / relationships / conversation_turns / Chroma collection; 与 reindex 互斥; 通过 admin router 前置 `Depends(get_current_user)` 自动鉴权 |
| v0.2.9 | 2026-07-19 | 关系基线 (`persona.relation.persona_addressing` / `user_addressing` / `context`) 抽入 TOML, 供 memory / relationship 两个 Agent 的 prompt 使用 |
| v0.2.10 | 2026-07-19 | 关系称呼动态演化: `relationships` 表新增 3 个 nullable 列 (`persona_addressing` / `user_addressing` / `context`) + `relationship_audit_log` 表; 新增 `PUT /panel/admin/relationship` 与 `GET /panel/admin/relationship/audit`; 关系分析 Agent 获得 `update_addressing` tool (自证 `reason` ≥ 10 字, source='agent'); 面板 `RelationshipsPage` 加编辑对话框与变更历史面板 (可"回退到此"); `RelationshipResponse` 恒返回当前有效值 (表 → TOML 基线回退) |
| v0.2.11 | 2026-07-19 | 人格配置面板编辑: 新增 `GET/PUT/DELETE /panel/admin/persona` 端点, 持久化 `data/persona_override.toml` (多层合并, 优先级: override > config.local [persona] > 资源默认); 运行时 `_reset_settings()` 热重载; 前端 `PromptsPage` 新增"人格编辑"标签页 (name / prompt / relation 三段编辑, 含保存与重置为默认) |
| v0.2.12 | 2026-07-20 | 面板首次登录强制改账号密码: 新增 `POST /panel/auth/setup-credentials` (同时改用户名 + 密码) 与 `require_password_settled` dependency; `must_change_password=True` 时 `/panel/admin/*` / `/panel/api-keys/*` / `/panel/admin/debug/*` 全部返回 `403 password_change_required`; 前端新增 `/setup` 页面 (BlankLayout) 与全局守卫强制跳转; `/settings` 精简为改密 + 只读用户名 + CLI 提示; `/v1/*` API Key 鉴权路径不受影响 |
| v0.3.0 | 2026-07-26 | 概念区分新增参与者/用户组两层 (服务器侧身份解析, 取代"最终用户由前端自行管理"); 新增 14 个 `/panel/admin/identity/*` 端点 (策略 CRUD / 参与者只读 / 用户组 / 绑定解绑) 与 `actor_id` 关系端点参数; `POST /panel/api-keys` 支持 `strategy_id`; 数据库表补 `identity.db` / `idempotency.db` / `notifications.db`; 详见 [modules/identity.md](modules/identity.md) |
| v0.3.3 | 2026-08-01 | 数据库表补 `persona.db` / `lorebook.db` / `space_policy.db` (结构化人格 / Lorebook / 空间策略) |
