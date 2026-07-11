# LLM 服务管理模块 | LLM Service Module

> **模块版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

LLM 服务管理模块负责管理**模型服务商**和**模型配置**的持久化，是 Mnemosync 调用模型能力的配置基础。

### 1.1 定位

> 本模块**不负责任何模型调用**——实际调用由 [Forwarder](forward.md) 完成。本模块只管理"有哪些服务商可用""每个 Agent 角色用哪个模型"这类配置数据。

```
CLI 命令（ad-service / set-main-model ...）
       │
       ▼
   LLMServiceStore ──→ SQLite（加密存储 API Key）
       │
       ▼
   Agent 执行时读取配置 → Forwarder 拿到 base_url + api_key + model → 调用服务商
```

### 1.2 核心概念

| 概念 | 说明 |
|------|------|
| **服务商 (LLMServiceProvider)** | 一个模型 API 提供方，含 base_url + api_key + id |
| **模型配置 (ModelConfiguration)** | 绑定到某服务商的具体模型，含角色（主/辅助） |
| **模型类型 (ModelType)** | `MAIN`（主模型）或 `ASSIST`（辅助模型） |

### 1.3 为什么需要这个模块

| 问题 | 解决方案 |
|------|----------|
| 模型选型不该写死在代码 | 持久化到数据库，CLI 动态配置 |
| API Key 不能明文存储 | Fernet 对称加密 |
| 需要支持多服务商 | 服务商表 + 外键关联模型配置 |
| 同一角色只能有一个模型 | service_id + model_type 唯一约束（覆盖更新） |

---

## 2. 数据模型 (Data Model)

### 2.1 LLMServiceProvider

```python
@dataclass
class LLMServiceProvider:
    id: str                    # 服务商唯一标识（如 "dashscope"）
    base_url: str              # API 基础 URL
    api_key: str               # 加密后的字符串
    created_at: datetime
    updated_at: datetime

    @property
    def api_key_masked(self) -> str:
        """脱敏显示：显示前 6 + 后 4 字符。"""
        ...
```

### 2.2 ModelConfiguration

```python
class ModelType(str, Enum):
    MAIN = "main"      # 主模型：主对话 Agent
    ASSIST = "assist"  # 辅助模型：记忆分析/关系分析/代理思考 Agent

@dataclass
class ModelConfiguration:
    id: str                    # 唯一标识（token_hex(16)）
    service_id: str            # 关联的服务商 ID（外键）
    model: str                 # 模型名称（如 "qwen-max"）
    model_type: ModelType      # MAIN / ASSIST
    created_at: datetime
    updated_at: datetime
```

### 2.3 关系

```
LLMServiceProvider (1) ──< ModelConfiguration (N)
                          ├─ MAIN 模型（最多 1 条）
                          └─ ASSIST 模型（最多 1 条）
```

> **约束**：同一 `service_id` + `model_type` 只允许一条记录，新设置覆盖旧配置。

---

## 3. 存储 (Storage)

### 3.1 SQLite Schema

```sql
-- 配置表（存储加密密钥等元数据）
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 服务商表
CREATE TABLE IF NOT EXISTS llm_services (
    id TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,    -- Fernet 加密
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_service_id ON llm_services(id);

-- 模型配置表
CREATE TABLE IF NOT EXISTS model_configs (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    model TEXT NOT NULL,
    model_type TEXT NOT NULL,            -- main / assist
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
);
CREATE INDEX idx_model_service ON model_configs(service_id);
CREATE INDEX idx_model_type ON model_configs(model_type);
```

### 3.2 API Key 加密（Fernet）

API Key 使用 **Fernet 对称加密**存储，加密密钥自动管理：

```
首次启动：
  1. 检查 config 表是否有 __encryption_key__
  2. 若无 → Fernet.generate_key() 生成新密钥 → 存入 config 表
  3. 若有 → 读取密钥

存储 API Key：
  plaintext → Fernet.encrypt() → base64 字符串 → 存入 llm_services.api_key_encrypted

读取 API Key：
  api_key_encrypted → Fernet.decrypt() → plaintext → 交给 Forwarder
```

**安全特性**：
- ✅ 加密密钥存于数据库 `config` 表，无需环境变量
- ✅ API Key 明文仅在内存中短暂存在（读取时解密）
- ✅ 数据库文件泄露时，密文无法直接还原（需同时拿到密钥）
- ⚠️ 加密密钥与数据同库——若需更高安全性，可改为环境变量管理密钥

### 3.3 存储接口

```python
class LLMServiceStore:
    async def save(service) -> LLMServiceProvider       # 新增服务商（id 重复抛错）
    async def get_by_id(service_id) -> LLMServiceProvider | None  # 读取（自动解密）
    async def list_all() -> list[LLMServiceProvider]    # 列出所有
    async def delete(service_id) -> bool                # 删除（级联删模型配置）
    async def exists(service_id) -> bool

    async def save_model(config) -> ModelConfiguration  # 保存模型配置（覆盖同类型）
    async def get_model(service_id, model_type) -> ModelConfiguration | None
    async def list_models(service_id) -> list[ModelConfiguration]
    async def get_main_model(service_id) -> ModelConfiguration | None
    async def get_assist_model(service_id) -> ModelConfiguration | None
```

---

## 4. CLI 命令

所有命令在 CLI 交互模式（`mnemosync login` 后）中执行。

### 4.1 服务商管理

#### `ad-service` — 添加服务商

```bash
Mnemosync > ad-service
Add new llm service provider:
Custom service id: dashscope
base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
API key: *******************

# 若 id 已存在
Custom service id: openai
This id has been already used!
```

#### `ls-service` — 列出所有服务商

```bash
Mnemosync > ls-service
service-id       base-url                                       api-key
dashscope        https://dashscope.aliyuncs.com/compatible-...   sk-****iYnX
openai           https://api.openai.com/v1                       sk-****enai
```

> API Key 脱敏显示（前 6 + 后 4）。

#### `rm-service [srv_id]` — 移除服务商

```bash
Mnemosync > rm-service openai
LLM service provider openai has been removed!
```

> 删除服务商会级联删除其所有模型配置。

#### `show-service` — 查看服务商详情

```bash
Mnemosync > show-service dashscope
service-id: dashscope
base-url:  https://dashscope.aliyuncs.com/compatible-mode/v1
api-key:   sk-abcd...wxyz
created:   2026-07-12T10:00:00Z
```

### 4.2 模型列表

#### `ls-models [srv_id]` — 列出可用模型

```bash
Mnemosync > ls-models dashscope
qwen-max
qwen-turbo
text-embedding-v3
gte-rerank
...
```

> 该命令通过 Forwarder 调用服务商的 `/models` 端点实时拉取，非本地缓存。

### 4.3 模型配置

#### `set-main-model [srv_id] [model]` — 设置主模型

```bash
Mnemosync > set-main-model dashscope qwen-max
Change main model to qwen-max from dashscope successfully!
```

#### `set-assist-model [srv_id] [model]` — 设置辅助模型

```bash
Mnemosync > set-assist-model dashscope qwen-turbo
Change assist model to qwen-turbo from dashscope successfully!
```

> 重复设置同一角色会覆盖原配置（service_id + model_type 唯一约束）。

#### `test-model [srv_id] [model]` — 测试连接

```bash
Mnemosync > test-model dashscope qwen-max
✓ Connection successful. Response time: 234ms.
```

---

## 5. 模型角色与使用

### 5.1 角色到 Agent 的映射

| 模型角色 | 使用者 | 推理方式 |
|----------|--------|----------|
| **MAIN** | 主对话 Agent | 直接推理 |
| **ASSIST** | 记忆分析 Agent | ReAct |
| **ASSIST** | 关系分析 Agent | CoT |
| **ASSIST** | 代理思考 Agent（可选） | CoT |

### 5.2 嵌入与重排序模型

嵌入模型和重排序模型不在 `model_configs` 表中独立配置——它们由所选服务商提供，通过服务商的 embedding/rerank API 端点调用，使用服务商默认模型。

```
向量检索 Agent:
  → 主对话 Agent / 记忆分析 Agent 触发工具调用
  → Forwarder 调用 service.base_url + "/embeddings"  （嵌入）
  → Forwarder 调用 service.base_url + "/rerank"      （重排序）
```

> 若服务商不支持 rerank，可降级为仅用 embedding cosine 粗筛（精度下降但可用）。

### 5.3 配置读取时机

```
启动时：
  1. 读取 main model 配置 → 验证可达性
  2. 读取 assist model 配置 → 验证可达性

Agent 执行时：
  1. 根据当前 Agent 角色读取对应模型配置
  2. 取出 service.api_key（解密）
  3. 组装 ForwarderConfig(base_url, api_key, model)
  4. 通过 Forwarder 发起请求
```

---

## 6. 错误处理

| 错误 | 异常 | 处理 |
|------|------|------|
| 服务商 id 已存在 | `ServiceAlreadyExistsError` | CLI 提示重输 |
| 服务商不存在 | `ServiceNotFoundError` | 设置模型配置时校验失败 |
| 模型未找到 | `ModelNotFoundError` | CLI 提示先 `ls-models` |
| API Key 解密失败 | `ValueError` | 加密密钥损坏或数据被篡改 |
| 服务商不可达 | Forwarder `UpstreamError` | `test-model` 时报错 |

---

## 7. 数据迁移

### 7.1 切换服务商

```bash
# 添加新服务商
Mnemosync > ad-service
Custom service id: siliconflow
...

# 重新设置模型（覆盖原配置）
Mnemosync > set-main-model siliconflow Qwen/Qwen3-72B
Mnemosync > set-assist-model siliconflow Qwen/Qwen3-8B

# 测试
Mnemosync > test-model siliconflow Qwen/Qwen3-72B

# 可选：移除旧服务商
Mnemosync > rm-service dashscope
```

### 7.2 切换嵌入模型

切换嵌入模型（embedding）需要重新生成 ChromaDB 全量向量：

```bash
# 1. 清空 ChromaDB collection
# 2. 从 SQLite 读取全部 MemoryEntry
# 3. 用新嵌入模型重新生成向量
# 4. 重新写入 ChromaDB
```

> 这个流程目前需要手动执行，未来版本会提供 `reindex` 命令。

---

## 8. 模块结构

```
src/
├── models/
│   └── llm_service.py          # LLMServiceProvider / ModelConfiguration / ModelType
├── storage/
│   └── llm_service_store.py    # LLMServiceStore（SQLite + Fernet 加密）
└── cli/
    └── cli_interactive.py       # CLI 命令实现
```

---

## 9. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [Forwarder](forward.md) | 读取本模块配置（base_url + api_key + model）发起调用 |
| [Agent 设计](agents.md) | 各 Agent 按角色使用 MAIN/ASSIST 模型 |
| [配置文档](../configuration.md) | 运行时配置的数据库路径等 |
| [CLI](cli.md) | CLI 命令的实现载体 |

---

## 10. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.2.0 | 2026-07-12 | 初始版本：服务商+模型配置管理、Fernet 加密、CLI 命令 |

---

> **维护者提示**:
> - API Key 加密密钥与数据同库——生产环境需做好数据库文件权限管控。
> - 切换嵌入模型必须重新索引 ChromaDB，否则向量维度不一致会导致检索失败。
> - `model_configs` 的 service_id + model_type 唯一约束是设计约束，不要放宽。