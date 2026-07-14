# API Key 管理模块

> **模块版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 概述

API Key 用于识别接入 Mnemosync 的**前端来源** (AstrBot / AIRI / Web 等)。所有 Key 共享同一份记忆池与同一人格配置——API Key 不做多用户/多人格隔离。

**定位速览**:

| 特性 | 说明 |
|------|------|
| 单人格 | 一个实例一套人格, API Key 不绑定 persona |
| 前端来源识别 | 通过 Key 备注区分是哪个前端接入 |
| 使用追踪 | 记录 `last_used_at`, 便于清理长期未用的 Key |
| 独立吊销 | 某个前端泄露只影响它自己的 Key |

**代码位置**:
- 数据模型 + SQLite 存储: [src/persistence/api_key_store.py](../../src/persistence/api_key_store.py)
- HTTP 端点: [src/api/routes/api_key.py](../../src/api/routes/api_key.py)
- CLI 管理命令: 交互式 shell (`mnemosync login`) 内

---

## 2. 数据模型

### 2.1 ApiKey

见 [api_key_store.py:13](../../src/persistence/api_key_store.py#L13):

```python
@dataclass
class ApiKey:
    id: str                        # UUID hex
    key_hash: str                  # 独立随机值, 不是 raw_key 的 hash
    key_prefix: str                # 前 12 字符, 用于展示
    note: str                      # 前端备注 (如 "AstrBot-QQ")
    created_at: datetime
    last_used_at: datetime | None
    is_active: bool
    key_full: str | None           # 仅创建时存储, 用于返回明文
```

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
    key_full     TEXT
);
CREATE INDEX idx_key_hash  ON api_keys(key_hash);
CREATE INDEX idx_is_active ON api_keys(is_active);
```

### 2.3 生成算法

```python
raw_key   = f"sk-{secrets.token_urlsafe(32)}"
key_hash  = secrets.token_hex(32)          # 独立随机值
key_prefix = raw_key[:12]
```

`key_hash` 与 raw_key 无数学关联——即使 raw_key 泄露也无法从 hash 反推出记录。校验时: `SqliteApiKeyStore.get_by_raw_key(raw_key)` 内部匹配 (实现细节见 store)。

---

## 3. HTTP 端点

路由前缀 `/api-keys` (真实定义见 [api_key.py:15](../../src/api/routes/api_key.py#L15))。

### POST /api-keys

**请求**:
```json
{ "note": "AstrBot-QQ 群" }
```

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
      "created_at": "...", "last_used_at": "...", "is_active": true
    }
  ]
}
```

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

验证在 [forward.py `_verify_api_key`](../../src/api/routes/forward.py#L63) 完成:

```
1. 从 Authorization 提取 raw_key
2. SqliteApiKeyStore.get_by_raw_key(raw_key)
3. 命中 → update_last_used → 继续处理
4. 未命中 → 401
```

**当前实现**: `_verify_api_key` 在 `create_chat_completion` 中未被调用 (预留), 实际请求鉴权尚未强制启用。生产部署前需在 `create_chat_completion` 顶部加上鉴权调用。

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
| `generate-key` | 生成新 Key, 交互式输入备注, 返回一次性明文 |
| `list-keys` | 列出所有 Key (仅 prefix) |
| `show-key <id>` | 查看单个 Key 详情 |
| `revoke-key <id>` | 吊销单个 Key |

具体实现见 [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py) `cmd_generate_key` 等。

---

## 6. 配置

Key 数据库路径固定为 `data/api_keys.db` ([api_key.py:17](../../src/api/routes/api_key.py#L17))。当前版本不支持通过环境变量覆盖。

---

## 7. 安全实践

1. 数据库只存 `key_hash` (独立随机值), 展示只用 `key_prefix`
2. `key_full` 仅创建时短暂存在于内存与响应中
3. 生产环境必须走 HTTPS
4. 每个前端独立 Key, 泄露只吊销该 Key
5. 定期 `list-keys` 检查 `last_used_at`, 清理长期未用的

---

## 8. 与其他模块

| 模块 | 关系 |
|------|------|
| [身份认证](../auth.md) | 管理员账号——用于登录管理面, 独立于 API Key |
| [消息处理](message-processing.md) | 请求鉴权入口 |
| [Forwarder](forward.md) | 鉴权通过后转发 |

---

## 9. 未来: 多人格

数据模型已预留升级路径, 需时可加 `persona_id` 字段将 Key 绑定到人格。当前 v0.2.1 未实现。

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-29 | 初始设计 |
| v0.2.0 | 2026-07-12 | 鉴权入口切到 LangGraph 编排 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 模块路径 `src/persistence/`, 撤销 API 契约 (单个 key_id), 路由前缀 `/api-keys` |
