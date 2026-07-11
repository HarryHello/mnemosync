# API Key 管理模块

> **模块版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

**API Key 管理模块** 为 Mnemosync 提供简单的访问控制能力，用于区分不同前端客户端来源。

### 1.1 核心定位

| 特性 | 说明 |
|------|------|
| **单人格架构** | 当前版本下，一个 Mnemosync 实例对应**单一人格配置** |
| **多前端区分** | 不同 API Key 用于标识请求来源（AstrBot / AIRI / Web 等） |
| **简单鉴权** | 防止未授权访问，非多用户认证系统 |
| **使用追踪** | 记录每个前端的使用情况，便于后续差异化清洗 |

### 1.2 设计原则

```
┌─────────────────────────────────────────────────────────────┐
│  重要：Mnemosync 当前是单人格架构                            │
│                                                              │
│  • 所有 API Key 共享同一份记忆池                             │
│  • 所有 API Key 使用同一人格配置                             │
│  • API Key 不用于人格隔离，仅用于前端来源识别                │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 使用场景

```
┌─────────────────────────────────────────────────────────────┐
│  场景：同一人格，多个前端                                   │
│                                                              │
│  Mnemosync 服务器配置：人格"墨小末"                          │
│                                                              │
│  ┌──────────────┐                                           │
│  │ API Key #1   │ → AstrBot 机器人 (QQ/微信)                │
│  │ sk-abc12345  │    提示词清洗策略：适配机器人格式         │
│  └──────────────┘                                           │
│                                                              │
│  ┌──────────────┐                                           │
│  │ API Key #2   │ → AIRI 桌宠                               │
│  │ sk-def67890  │    提示词清洗策略：适配桌宠交互           │
│  └──────────────┘                                           │
│                                                              │
│  ┌──────────────┐                                           │
│  │ API Key #3   │ → Web 聊天室                              │
│  │ sk-ghi11111  │    提示词清洗策略：适配网页聊天           │
│  └──────────────┘                                           │
│                                                              │
│  所有前端共享"墨小末"的记忆和人格配置                        │
└─────────────────────────────────────────────────────────────┘
```

### 1.4 为什么需要多个 API Key？

| 原因 | 说明 | 示例 |
|------|------|------|
| **来源识别** | 知道请求来自哪个前端 | 机器人 vs 桌宠 vs 网页 |
| **差异化清洗** | 不同前端需要不同提示词处理 | 机器人需要去除 QQ 号，桌宠需要保留表情 |
| **独立撤销** | 某个前端泄露不影响其他 | AstrBot Key 泄露，仅撤销它 |
| **使用统计** | 追踪各前端使用频率 | 哪个前端最活跃 |
| **未来扩展** | 为多人格架构预留设计 | 后续可支持 Key→Persona 绑定 |

---

## 2. 模块结构 (Module Structure)

```
src/
├── storage/
│   ├── models.py                  # ApiKey 数据模型
│   ├── base.py                    # ApiKeyStore 协议
│   └── sqlite.py                  # SQLite 实现
│
├── api/
│   ├── routes/
│   │   └── api_key.py             # API Key CRUD 端点
│   └── schemas/
│       └── api_key.py             # Pydantic 请求/响应模型
│
└── cli/
    └── cli.py                     # CLI 管理命令
```

> **注意**：API Key 模块当前直接位于 `storage/` 层，因为它是简单的数据 CRUD，
> 不涉及复杂的认证逻辑。未来多人格架构时可能需要重构。

---

## 3. 数据模型 (Data Model)

### 3.1 ApiKey 实体

```python
@dataclass
class ApiKey:
    id: str                    # 唯一标识 (UUID hex)
    key_hash: str              # 用于验证的哈希 (非明文)
    key_prefix: str            # 前 12 字符用于展示 (如 "sk-abc12345")
    note: str                  # 前端备注 (如 "AstrBot-QQ 群")
    created_at: datetime       # 创建时间
    last_used_at: datetime | None  # 最后使用时间
    is_active: bool            # 是否启用
    key_full: str | None       # 完整 Key (仅生成时存储)
```

> **注意**：当前版本 `ApiKey` 不包含 `persona_id` 字段，因为所有 Key 共享同一人格配置。

### 3.2 数据库 Schema

```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1,
    key_full TEXT
);

-- 索引优化
CREATE INDEX idx_key_hash ON api_keys(key_hash);
CREATE INDEX idx_is_active ON api_keys(is_active);
```

### 3.3 Key 生成算法

```python
@staticmethod
def generate(note: str) -> "ApiKey":
    """生成新的 API Key."""
    # 1. 生成完整 Key: sk-<32 字节 URL-safe Base64>
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    
    # 2. 生成验证哈希：独立的 32 字节随机值
    #    注意：不是对 raw_key 做 hash，而是独立随机值
    #    原因：避免 raw_key 泄露后反推数据库记录
    key_hash = secrets.token_hex(32)
    
    # 3. 提取前缀用于展示
    key_prefix = raw_key[:12]
    
    return ApiKey(
        id=secrets.token_hex(16),
        key_hash=key_hash,
        key_prefix=key_prefix,
        note=note,
        key_full=raw_key,  # 仅在创建时存储
    )
```

---

## 4. API 端点 (API Endpoints)

### 4.1 创建 API Key

```http
POST /api-keys
Content-Type: application/json

{
  "note": "用于 AstrBot 机器人"
}
```

**响应 (201 Created)**:
```json
{
  "id": "a1b2c3d4e5f6...",
  "key": "sk-xK9mN2pL5qR8sT1vW4yZ7aB0cD3eF6gH",
  "key_prefix": "sk-xK9mN2pL",
  "note": "用于 AstrBot 机器人",
  "created_at": "2026-03-29T10:00:00Z"
}
```

> ⚠️ **重要**：`key` 字段仅在创建时返回一次，后续无法获取

---

### 4.2 列出 API Keys

```http
GET /api-keys
```

**响应 (200 OK)**:
```json
[
  {
    "id": "a1b2c3d4e5f6...",
    "key_prefix": "sk-xK9mN2pL",
    "note": "用于 AstrBot 机器人",
    "created_at": "2026-03-29T10:00:00Z",
    "last_used_at": "2026-03-29T12:30:00Z",
    "is_active": true
  },
  {
    "id": "b2c3d4e5f6g7...",
    "key_prefix": "sk-yL0nO3qM",
    "note": "用于 AIRI 桌宠",
    "created_at": "2026-03-28T08:00:00Z",
    "last_used_at": null,
    "is_active": false
  }
]
```

---

### 4.3 撤销 API Key

```http
DELETE /api-keys/{key_id}
```

**响应 (204 No Content)**

---

### 4.4 批量撤销

```http
POST /api-keys/revoke
Content-Type: application/json

{
  "key_ids": ["a1b2c3d4e5f6...", "b2c3d4e5f6g7..."]
}
```

---

## 5. 认证流程 (Authentication Flow)

### 5.1 请求处理

```
┌─────────────────────────────────────────────────────────────┐
│  1. 客户端请求                                              │
│  POST /v1/chat/completions                                  │
│  Authorization: Bearer sk-xK9mN2pL5qR8sT1vW4yZ7aB0cD3eF6gH  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  2. 提取并验证 Key                                          │
│  - 从 Authorization header 提取 Bearer Token                │
│  - 验证格式：必须以 "sk-" 开头                              │
│  - 查询数据库：SELECT * FROM api_keys WHERE key_hash = ?    │
│  - 检查 is_active = 1                                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
                    ┌───────┴───────┐
                    ↓               ↓
            ┌───────────┐   ┌───────────┐
            │ 验证通过   │   │ 验证失败   │
            └───────────┘   └───────────┘
                    ↓               ↓
            继续处理请求      返回 401 Unauthorized
                    ↓
┌─────────────────────────────────────────────────────────────┐
│  3. 记录使用情况 (异步)                                     │
│  UPDATE api_keys SET last_used_at = NOW() WHERE id = ?      │
└─────────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────────┐
│  4. 加载人格配置                                            │
│  - 当前版本：所有 Key 使用同一人格配置                       │
│  - 未来版本：可能根据 Key 绑定不同 Persona                   │
└─────────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────────┐
│  5. 触发 LangGraph StateGraph                                │
│  - v0.2.0：鉴权后进入多 Agent 编排（非旧的确定性管道）       │
│  - parse_request → (proxy_thinking) → main_dialogue → ...    │
└─────────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────────┐
│  6. 主对话 Agent 通过 Forwarder 调用上游模型                 │
│  - 加载记忆 + 人格拼装上下文                                 │
│  - POST https://api.openai.com/v1/chat/completions           │
│  - SSE 流式透传给前端                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. CLI 管理命令

```bash
# 列出所有 API Keys
$ mnemosync ls-keys

# 生成新 Key
$ mnemosync generate-key
It is recommanded to map one key to one platform.
Please enter the annotation for the new key:
> AstrBot-QQ 群

Your new api-key is:
sk-qwertyuiopasdfghjklzxcvbnm

Do not let others get your keys!

# 查看 Key 详情
$ mnemosync show-key a1b2c3d4e5f6...

sk-abcdabcdabcdabcdabcdabcdabcd

Annotation: AstrBot-QQ 群

Do not let others get your keys!

# 撤销 Key
$ mnemosync revoke a1b2c3d4e5f6...
Revoked key a1b2c3d4e5f6...
```

---

## 7. 配置项 (Configuration)

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `MNEMOSYNC_DB_PATH` | `data/api_keys.db` | API Key 数据库路径 |

---

## 8. 安全最佳实践 (Security Best Practices)

### 8.1 Key 存储

- ✅ 数据库存储 `key_hash` 而非明文
- ✅ `key_full` 仅在创建时写入，之后不再读取
- ✅ 前端展示使用 `key_prefix`

### 8.2 Key 传输

- ✅ 仅通过 HTTPS 传输（生产环境）
- ✅ 使用 `Authorization: Bearer` header
- ✅ 不在 URL、日志中暴露完整 Key

### 8.3 Key 管理

- ✅ 为每个前端创建独立 Key
- ✅ 泄露时立即撤销并生成新 Key
- ✅ 定期清理长期未用的 Key

---

## 9. 与多人格架构的关系 (Future: Multi-Persona)

### 9.1 当前架构 (v0.x)

```
┌─────────────────────────────────────┐
│  Mnemosync 实例                      │
│  ┌─────────────────────────────┐    │
│  │  人格配置："墨小末"          │    │
│  └─────────────────────────────┘    │
│         ↑ ↑ ↑                        │
│         │ │ │                        │
│  ┌──────┘ │ └──────┐                │
│  │        │        │                │
│ Key#1   Key#2   Key#3               │
│ (AstrBot)(AIRI) (Web)               │
└─────────────────────────────────────┘
```

### 9.2 未来架构 (v1.0+ 规划)

```
┌─────────────────────────────────────┐
│  Mnemosync 实例                      │
│  ┌─────────────┐ ┌─────────────┐    │
│  │ 人格 A       │ │ 人格 B       │    │
│  │ "墨小末"     │ │ "艾丽"       │    │
│  └─────────────┘ └─────────────┘    │
│         ↑                 ↑          │
│         │                 │          │
│  ┌──────┴──────┐   ┌──────┴──────┐  │
│  │             │   │             │  │
│ Key#1,2,3    Key#4           Key#5 │
│ (绑定人格 A)  (绑定人格 B)    (绑定人格 B)│
└─────────────────────────────────────┘
```

### 9.3 升级路径

当需要支持多人格时，数据库迁移：

```sql
-- 添加 persona_id 字段
ALTER TABLE api_keys ADD COLUMN persona_id TEXT;

-- 将现有 Key 关联到默认人格
UPDATE api_keys SET persona_id = 'default';

-- 添加外键约束
ALTER TABLE api_keys ADD CONSTRAINT fk_persona
    FOREIGN KEY (persona_id) REFERENCES personas(id);
```

---

## 10. 与其他模块的关系 (Relationships)

```mermaid
graph LR
    A[API Key 模块] --> B[认证中间件]
    A --> C[CLI 工具]
    A --> D[人格配置]
    
    B -->|验证 Key| A
    C -->|管理 Key| A
    D -->|当前共享 | A
    D -.->|未来绑定 | A
    
    style A fill:#e1f5ff
    style B fill:#f0e1ff
    style C fill:#e1ffe1
    style D fill:#fff4e1
```

- **认证中间件**: 使用 API Key 进行请求鉴权
- **CLI 工具**: 提供命令行管理接口
- **人格配置**: 当前所有 Key 共享同一配置，未来可能绑定

---

## 11. 版本历史 (Version History)

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1.0 | 2026-03-29 | 初始设计：单人格架构下的多前端 Key 管理 |
| v0.2.0 | 2026-07-12 | 认证流程更新：鉴权后触发 LangGraph StateGraph（替代旧的清洗管道） |

---

> **维护者提示**:
> - API Key 是系统安全的第一道防线，任何修改必须经过严格审查
> - 禁止在日志中打印完整 API Key
> - 当前设计为单人格架构，多人格支持需重新设计数据模型
