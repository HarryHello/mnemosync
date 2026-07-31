# API Key 管理模块

> **模块版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

API Key 用于识别接入 Mnemosync 的**前端来源** (AstrBot / AIRI / Web 等)。所有 Key 共享**服务器端定义的统一人格**——人格由服务器权威持有, 不由客户端请求传入, API Key 不绑定 persona。

v0.3.0 起 API Key 新增**身份策略绑定** (`strategy_id`): 策略定义如何从请求中识别参与者, 是"单人格多用户"的入口机制。未绑定策略的 Key 进入非归属模式。详见 [身份管理模块](identity.md)。

**定位速览**:

| 特性 | 说明 |
|------|------|
| 单人格 | 一个实例一套人格, 由服务器端权威定义; API Key 不绑定 persona, 客户端不传入人格 |
| 前端来源识别 | 通过 Key 备注区分是哪个前端接入 |
| 身份策略绑定 (v0.3.0) | `strategy_id` 绑定身份识别策略; 未绑定 → 非归属模式 (不建身份、不读写私有记忆) |
| 使用追踪 | 记录 `last_used_at`, 便于清理长期未用的 Key |
| 独立吊销 | 某个前端泄露只影响它自己的 Key |

**代码位置**:
- 数据模型 + SQLite 存储: [src/persistence/api_key_store.py](../../src/persistence/api_key_store.py)
- HTTP 端点: [src/api/routes/api_key.py](../../src/api/routes/api_key.py)
- CLI 管理命令: 交互式 shell (`mnemosync login`) 内; 身份策略另有 `mnemosync identity` 命令组 ([identity_cmd.py](../../src/cli/identity_cmd.py))

---

## 2. 数据模型

### 2.1 ApiKey

见 [api_key_store.py](../../src/persistence/api_key_store.py):

```python
@dataclass
class ApiKey:
    id: str                        # UUID hex
    key_hash: str                  # 独立随机值, 不是 raw_key 的 hash
    key_prefix: str                # 前 12 字符, 用于展示
    note: str                      # 前端备注 (如 "AstrBot-QQ"), 派生 source_frontend
    created_at: datetime
    last_used_at: datetime | None
    is_active: bool
    key_full: str | None           # 仅创建时存储, 用于返回明文
    source: str = "user"           # v0.2.5: user / panel-debug
    strategy_id: str | None = None # v0.3.0: 绑定的身份策略 ID
```

`source` 字段 (v0.2.5) 区分 Key 的来源:
- `user` — 用户通过 `/panel/api-keys` 或 CLI 手动创建
- `panel-debug` — 调试面板自动生成 (仅内部使用, 不在用户 Key 列表中展示, 也不能通过用户 API 撤销)

`strategy_id` 字段 (v0.3.0) 指向 `identity_strategies.id` (无外键约束, 策略删除后引用悬空 → 该 Key 退回非归属模式)。

### 2.2 Schema

```sql
CREATE TABLE api_keys (
    id           TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL UNIQUE,
    key_prefix   TEXT NOT NULL,
    note         TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    is_active    INTEGER NOT NULL DEFAULT 1,
    key_full     TEXT,
    key_encrypted TEXT,                                    -- v0.2.3
    source       TEXT NOT NULL DEFAULT 'user',             -- v0.2.5
    strategy_id  TEXT                                      -- v0.3.0
);
CREATE INDEX idx_key_hash  ON api_keys(key_hash);
CREATE INDEX idx_is_active ON api_keys(is_active);
CREATE INDEX idx_source    ON api_keys(source);           -- v0.2.5
```

### 2.3 生成算法

```python
raw_key   = f"sk-{secrets.token_urlsafe(32)}"
key_hash  = secrets.token_hex(32)          # 独立随机值
key_prefix = raw_key[:12]
```

`key_hash` 与 raw_key 无数学关联——即使 raw_key 泄露也无法从 hash 反推出记录。校验时: `SqliteApiKeyStore.get_by_raw_key(raw_key)` 内部匹配 (实现细节见 store)。

`ApiKey.generate(note, strategy_id=None)` (v0.3.0) 在生成时可绑定策略。

---

## 3. HTTP 端点

路由前缀 `/api-keys` (真实定义见 [api_key.py](../../src/api/routes/api_key.py))。

### POST /api-keys

**请求**:
```json
{ "note": "AstrBot-QQ 群", "strategy_id": "strategy_bbf3280f..." }
```

`strategy_id` 可选 (v0.3.0); 省略或 null → 该 Key 的请求进入非归属模式。

**响应 (201)**:
```json
{
  "id": "a1b2c3d4...",
  "key": "sk-xK9mN2pL5qR8sT1vW4yZ7aB0cD3eF6gH",
  "key_prefix": "sk-xK9mN2pL",
  "note": "AstrBot-QQ 群",
  "created_at": "2026-07-15T10:00:00Z"
}
```

**注意**: `key` 只在创建时返回一次。

### GET /api-keys

**响应 (200)**:
```json
{
  "items": [
    {
      "id": "...", "key_prefix": "sk-...", "note": "...",
      "created_at": "...", "last_used_at": "...", "is_active": true,
      "strategy_id": "strategy_..." 
    }
  ]
}
```

`strategy_id` 为 null 表示未绑定 (v0.3.0)。

### DELETE /api-keys/{key_id}

吊销单个 Key。找不到返回 404, 成功返回 204。

### POST /api-keys/revoke

请求体形式吊销:

```json
{ "key_id": "a1b2c3d4..." }
```

**注意**: 当前实现**只接受单个 `key_id`** (见 [ApiKeyRevokeRequest](../../src/api/schemas/api_key.py)), 不是批量。如需批量, 需多次调用。

---

## 4. 认证使用

前端携带 API Key 调 `/v1/chat/completions`:

```
Authorization: Bearer sk-xK9mN2pL5qR8sT1vW4yZ7aB0cD3eF6gH
```

验证在 [forward.py `_verify_api_key`](../../src/api/routes/forward.py) 完成, 在 `create_chat_completion` 顶部**已启用**:

```
1. 从 Authorization 提取 raw_key
2. SqliteApiKeyStore.get_by_raw_key(raw_key)
3. 命中 → update_last_used → 拿到完整 ApiKey 对象 (含 strategy_id)
4. 未命中 → 返回 None (下游流程继续, source_frontend 与身份解析均缺省)
```

**身份解析 (v0.3.0)**: 鉴权后 `_resolve_identity_context` 读取 `api_key.strategy_id`, 加载策略并按类型从请求中提取参与者身份, 产出 `IdentityContext` (effective_user_id / actor_id / space_id / channel_type)。无绑定策略或解析失败 → 非归属模式。完整流程见 [身份管理模块 §4](identity.md#4-解析流程)。

**source_frontend 派生 (v0.2.6)**: `_resolve_source_frontend(request, api_key_id)` 读取 `api_key.note`, 作为服务端派生的 `source_frontend` 元数据字段——写入 `conversation_turns.source_frontend` 列, 用于跨前端调试与追溯。客户端无法伪造该值 (不读客户端 header)。见 [message-processing.md §3](message-processing.md) 与 [memory-system.md](memory-system.md)。

鉴权通过后进入 [消息处理流程](message-processing.md)。

---

## 5. CLI 管理

顶层 `mnemosync` 命令**不含** Key 管理; 需先进入交互式 shell:

```bash
mnemosync login
```

在 shell 内:

| 命令 | 说明 |
|------|------|
| `generate-key` | 生成新 Key (source=user), 交互式输入备注, 返回一次性明文 |
| `list-keys` | 列出所有 source=user 的 Key (仅 prefix); panel-debug 生成的 Key 不入列表 |
| `show-key <id>` | 查看单个 Key 详情 |
| `revoke-key <id>` | 吊销单个 Key |

具体实现见 [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py) `cmd_generate_key` 等。

身份策略的管理不在交互式 shell 内, 而是顶层命令组:

```bash
mnemosync identity strategy list / create / show / update / delete
```

见 [src/cli/identity_cmd.py](../../src/cli/identity_cmd.py) 与 [身份管理模块 §7](identity.md#7-cli)。面板「API Key」页创建 Key 时也可直接下拉绑定策略。

---

## 6. 配置

Key 数据库路径固定为 `data/api_keys.db`。当前版本不支持通过环境变量覆盖。

---

## 7. 安全实践

1. 数据库只存 `key_hash` (独立随机值), 展示只用 `key_prefix`
2. `key_full` 仅创建时短暂存在于内存与响应中
3. 生产环境必须走 HTTPS
4. 每个前端独立 Key, 泄露只吊销该 Key
5. 定期 `list-keys` 检查 `last_used_at`, 清理长期未用的
6. 身份策略由服务器执行, 客户端无法声明或伪造身份 (见 [身份管理模块](identity.md))

---

## 8. 与其他模块

| 模块 | 关系 |
|------|------|
| [身份认证](../auth.md) | 管理员账号——用于登录管理面, 独立于 API Key |
| [身份管理](identity.md) | `strategy_id` 绑定身份策略 (v0.3.0 多用户入口) |
| [消息处理](message-processing.md) | 请求鉴权入口 |
| [Forwarder](forward.md) | 鉴权通过后转发 |

---

## 9. 未来: 多人格

数据模型已预留升级路径, 需时可加 `persona_id` 字段将 Key 绑定到人格。v0.3.0 已实现的是**单人格多用户** (Key 经 `strategy_id` 绑定身份策略), 多人格仍未实现。

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-29 | 初始设计 |
| v0.2.0 | 2026-07-12 | 鉴权入口切到 LangGraph 编排 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 模块路径 `src/persistence/`, 撤销 API 契约 (单个 key_id), 路由前缀 `/api-keys` |
| v0.2.5 | 2026-07-18 | 新增 `source` 列 (`user` / `panel-debug`); `/panel/api-keys` 只列 `source=user`; 调试面板自动生成的 Key 不可通过用户 API 撤销 |
| v0.2.6 | 2026-07-18 | 与代码对齐: `_verify_api_key` 已在 `create_chat_completion` 顶部启用; `_resolve_source_frontend` 从 `api_key.note` 派生 `source_frontend`, 写入 `conversation_turns.source_frontend` (客户端无法伪造) |
| v0.3.0 | 2026-07-26 | 新增 `strategy_id` 列: API Key 绑定身份识别策略, 未绑定进入非归属模式; 创建请求/列表响应携带 `strategy_id`; 面板创建对话框支持策略下拉; 文档指向新 [身份管理模块](identity.md) |
