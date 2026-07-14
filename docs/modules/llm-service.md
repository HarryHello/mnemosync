# LLM 服务管理模块 | LLM Service Module

> **模块版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-12
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 概述

LLM 服务管理模块存储**服务商凭证**与**模型角色配置**, 并以对称加密保护 API Key。真实的模型调用交由 [Forwarder](forward.md) 完成——本模块只回答 "有哪些服务商可用" 和 "MAIN/ASSIST 角色分别用哪个服务商的哪个模型"。

**代码位置**: [src/infra/llm_service/](../../src/infra/llm_service/) (`models.py` / `store.py`)。

**与 `config.local.toml` 的关系**: 当前主运行路径 (`get_settings()`) 直接读 `config.local.toml` 的 `[chat]` / `[embedding]` / `[rerank]` 段, 不走本模块。本模块是 CLI 命令 `ad-service` / `set-main-model` 等交互式流程使用的持久化后端, 用于将来支持多服务商并存与热切换。

---

## 2. 数据模型

见 [models.py](../../src/infra/llm_service/models.py):

### 2.1 ModelType

```python
class ModelType(str, Enum):
    MAIN   = "main"     # 主对话
    ASSIST = "assist"   # 记忆/关系/代理思考
```

### 2.2 LLMServiceProvider

```python
@dataclass
class LLMServiceProvider:
    id: str                # "dashscope" / "openai" / ...
    base_url: str
    api_key: str           # 内存中为明文; 存储时加密
    created_at: datetime
    updated_at: datetime

    def api_key_masked(self) -> str: ...   # 前 6 + 后 4
```

### 2.3 ModelConfiguration

```python
@dataclass
class ModelConfiguration:
    id: str
    service_id: str        # 外键
    model: str
    model_type: ModelType  # MAIN 或 ASSIST
    created_at: datetime
    updated_at: datetime
```

**唯一约束**: (`service_id`, `model_type`) 唯一; 同一角色再次 set 会覆盖上一条。

---

## 3. 存储

**位置**: [src/infra/llm_service/store.py](../../src/infra/llm_service/store.py) `LLMServiceStore`, 数据库 `data/llm_service.db` (可通过 `[storage].llm_db_path` 覆盖)。

### 3.1 Schema

```sql
CREATE TABLE config (
    key TEXT PRIMARY KEY, value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE llm_services (
    id TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE model_configs (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    model TEXT NOT NULL,
    model_type TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
);
```

### 3.2 加密

Fernet 对称加密。密钥策略:
1. 首次访问 `config` 表读 `__encryption_key__`, 缺则生成一次 (`Fernet.generate_key()`) 并写回
2. 存 `api_key` 时 encrypt, 读取时 decrypt
3. 密钥与密文同库——数据库单文件泄露即失去加密防护; 生产需做文件权限管控或改环境变量托管

### 3.3 接口

真实方法名 (见 store.py):

```python
class LLMServiceStore:
    async def init_db()
    async def save_service(service)      -> LLMServiceProvider
    async def get_service(service_id)    -> LLMServiceProvider | None
    async def list_services()            -> list[LLMServiceProvider]
    async def delete_service(service_id) -> bool
    async def save_model(config)         -> ModelConfiguration
    async def get_model(service_id, model_type) -> ModelConfiguration | None
    async def list_models(service_id)    -> list[ModelConfiguration]
```

**注意**: 方法名是 `save_service` / `delete_service`, 不是历史文档中提到的 `save` / `delete`。

---

## 4. CLI 命令

在 `mnemosync login` 进入交互式 shell 后可用:

| 命令 | 说明 |
|------|------|
| `ad-service` | 添加服务商 (交互输入 id / base_url / api_key) |
| `ls-service` | 列出服务商 (脱敏 API Key) |
| `show-service <id>` | 查看服务商详情 |
| `rm-service <id>` | 删除服务商 + 级联删除模型配置 |
| `ls-models <id>` | 通过 Forwarder 拉取服务商 `/models` 端点 |
| `set-main-model <id> <model>` | 设置主模型 |
| `set-assist-model <id> <model>` | 设置辅助模型 |
| `set-embedding-model <id> <model>` | 更新 `config.local.toml` 的嵌入模型 |
| `test-model <id> <model>` | 探活 |

具体实现见 [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py)。

---

## 5. 与 Agent 的映射

| Agent | 使用角色 |
|-------|---------|
| 主对话 | MAIN |
| 记忆分析 | ASSIST (需支持 function_call) |
| 关系分析 | ASSIST |
| 代理思考 | ASSIST |

**嵌入 / 重排模型不在 `model_configs`**——它们由 `[embedding]` / `[rerank]` 段直接配置, 因为通常与主 chat 使用不同的端点 (尤其 DashScope)。参见 [configuration.md](../configuration.md)。

---

## 6. 错误

| 情境 | 处理 |
|------|------|
| 服务商 id 重复 | `save_service` 报错, CLI 提示重输 |
| 服务商不存在 | `save_model` 校验失败 |
| API Key 解密失败 | `ValueError` (密钥损坏或数据被篡改) |
| 服务商不可达 | `test-model` 走 Forwarder → 抛 `UpstreamError` |

---

## 7. 模块结构

```
src/infra/llm_service/
├── __init__.py
├── models.py    # LLMServiceProvider / ModelConfiguration / ModelType
└── store.py     # LLMServiceStore (SQLite + Fernet)
```

---

## 8. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [Forwarder](forward.md) | 消费本模块 (或 config.local.toml) 得到的 base_url + api_key + model |
| [配置](../configuration.md) | 主运行路径的实际配置源; 本模块是可选增强 |
| [CLI](cli.md) | 交互命令的实现载体 |

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 初始: 服务商 + 模型配置 + Fernet 加密 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 模块路径 `src/infra/llm_service/`, 方法名 `save_service` / `delete_service`, 与 config.local.toml 的关系说明 |
