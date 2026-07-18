# LLM 服务管理模块 | LLM Service Module

> **模块版本**: v0.2.6
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-12
> **最后更新**: 2026-07-18
> **作者**: HarryHelloo

---

## 1. 概述

LLM 服务管理模块存储**服务商凭证** (`services` 表) 与**角色 → 候选优先级列表** (`role_bindings` 表), 以对称加密保护 API Key。真实的模型调用交由 [Forwarder / MultiForwarder](forward.md) 完成 — 本模块只回答 "有哪些服务商可用" 和 "MAIN/ASSIST/EMBEDDING/RERANK 各角色按什么顺序尝试哪个服务商的哪个模型"。

**v0.2.3 起, `role_bindings` 是模型绑定的唯一真相源** — `config.local.toml` 里旧的 `[chat]` / `[embedding]` / `[rerank]` 段已废弃, `get_settings()` 也不再读它们, `RoleResolver` 直接从这两张表构造 `ResolvedCandidate`。

**代码位置**:
- [src/infra/llm_service/models.py](../../src/infra/llm_service/models.py) — dataclass 定义
- [src/infra/llm_service/store.py](../../src/infra/llm_service/store.py) — SQLite 存储
- [src/core/models/resolver.py](../../src/core/models/resolver.py) — `RoleResolver`

---

## 2. 数据模型

见 [models.py](../../src/infra/llm_service/models.py):

### 2.1 ModelType

```python
class ModelType(str, Enum):
    MAIN      = "main"       # 主对话
    ASSIST    = "assist"     # 记忆/关系/代理思考/提示词清洗
    EMBEDDING = "embedding"  # 文本 → 向量 (v0.2.3)
    RERANK    = "rerank"     # 检索精排 (v0.2.3)
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

### 2.3 RoleBinding

v0.2.3 用它替代原来的 `ModelConfiguration`:

```python
@dataclass
class RoleBinding:
    role: ModelType
    priority: int             # 0 = 最高优先级
    service_id: str
    model: str
    created_at: datetime
    context_length: int | None = None   # v0.2.4: 面板展示
    embedding_dim: int | None = None    # v0.2.4: 向量库维度锁
    send_dimensions: bool = False       # v0.2.8: 是否把 embedding_dim 作为 dimensions 参数发上游
```

PRIMARY KEY `(role, priority)`。同角色多条 = 优先级列表; 上游失败时 MultiForwarder 按 priority 顺序 fallback (**嵌入角色除外**, 见 §2.5)。

### 2.4 ResolvedCandidate

`RoleResolver` 把 `RoleBinding` 与 `LLMServiceProvider` join 后的结果:

```python
@dataclass
class ResolvedCandidate:
    role: ModelType
    priority: int
    service_id: str
    base_url: str
    api_key: str          # 已解密
    model: str
    context_length: int | None = None
    embedding_dim: int | None = None
    send_dimensions: bool = False       # v0.2.8
```

`context_length` 会被 `build_short_term_history` 用作双窗装填的模型窗预算 (v0.2.6, 见 [memory-system.md §1.4](memory-system.md#14-短期记忆-v026--跨前端对话流水))。

### 2.5 嵌入角色单绑定 (v0.2.4)

`add_role_binding(role=EMBEDDING, ...)` 前先 `SELECT COUNT(*) FROM role_bindings WHERE role='embedding'`, 已有绑定则 `raise ValueError("嵌入模型只允许一条绑定, 请先删除现有绑定")`。`reorder_role_bindings(EMBEDDING)` 直接 raise (单绑定无排序意义)。

理由: 不同嵌入模型输出的向量空间语义不同, 换模型会让已存向量瞬间失效; ChromaDB collection 首次写入时锁定 `(service_id, model, dim)`, 之后每次写入前 assert 一致。想换模型必须走 Reindex。见 [dev-decisions.md 嵌入模型单绑定 + Reindex + Prune](../dev-decisions.md)。

### 2.6 `send_dimensions` 透传开关 (v0.2.8)

`embedding_dim` 有两条独立职责, v0.2.8 把它们拆开:

| 职责 | 落点 | 开关 |
|------|------|------|
| **向量库维度锁** | Chroma collection metadata (`service_id / model / dim`), Reindex assert | 存了就锁, 无开关 |
| **透传 `dimensions` 参数给上游** | HTTP body `{"dimensions": N}` | `send_dimensions=True` 才发 |

为什么要拆: 只有可变维模型接受 `dimensions` 参数 (OpenAI Matryoshka Representation Learning), 常见白名单:

- OpenAI: `text-embedding-3-small`, `text-embedding-3-large`
- DashScope: `text-embedding-v3`, `text-embedding-v4`, `qwen3-embedding-*`

其他一律固定维 (bge / bce / jina / mistral / gemini / text-embedding-ada-002 等), 显式传 `dimensions` 会被拒 (SiliconFlow: `20015 The parameter is invalid`; OpenAI: 400 unsupported)。

`MultiForwarder.embed` 的门控 (见 [src/infra/forwarder/multi.py](../../src/infra/forwarder/multi.py) `embed`):

```python
if dimensions is not None:            # 显式传参无条件透传 (probe-dimension 走这里)
    effective_dim = dimensions
elif c.send_dimensions:                # 绑定显式开启
    effective_dim = c.embedding_dim
else:                                  # 默认关: 不透传, 兼容固定维模型
    effective_dim = None
return await fwd.embed(..., dimensions=effective_dim)
```

CLI `model add embedding <svc> <model> --dim N` 默认 **不透传**; 显式加 `--send-dim` 才透传。面板 `ModelsPage` 添加嵌入模型的表单里有对应勾选框, 仅在填了维度时可勾。

参考: Cherry Studio 同样问题的 [PR #8086](https://github.com/CherryHQ/cherry-studio/pull/8086) 引入 `isAutoDimensions` 布尔, 是等价方案 (布尔取反关系)。

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

CREATE TABLE services (
    id TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE role_bindings (
    role TEXT NOT NULL,
    priority INTEGER NOT NULL,
    service_id TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    context_length INTEGER,                            -- v0.2.4
    embedding_dim INTEGER,                             -- v0.2.4 (向量库维度锁)
    send_dimensions INTEGER NOT NULL DEFAULT 0,        -- v0.2.8 (是否透传给上游)
    PRIMARY KEY (role, priority),
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
);
```

Schema 增列走 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 幂等升级 (`_ensure_role_binding_columns()`), 无独立 migration 目录。

### 3.2 加密

Fernet 对称加密。密钥策略:
1. 首次访问 `config` 表读 `__encryption_key__`, 缺则生成一次 (`Fernet.generate_key()`) 并写回
2. 存 `api_key` 时 encrypt, 读取时 decrypt
3. 密钥与密文同库 — 数据库单文件泄露即失去加密防护; 生产需做文件权限管控或改环境变量托管

### 3.3 接口

真实方法名 (见 store.py):

```python
class LLMServiceStore:
    async def init_db()
    async def save_service(service)      -> LLMServiceProvider
    async def get_service(service_id)    -> LLMServiceProvider | None
    async def list_services()            -> list[LLMServiceProvider]
    async def delete_service(service_id) -> bool

    async def add_role_binding(role, service_id, model, *,
                                context_length=None, embedding_dim=None,
                                send_dimensions=False) -> RoleBinding
    async def list_role_bindings(role: ModelType | None = None) -> list[RoleBinding]
    async def delete_role_binding(role, priority) -> bool
    async def reorder_role_bindings(role, priority_order: list[int]) -> None
```

---

## 4. RoleResolver

**位置**: [src/core/models/resolver.py](../../src/core/models/resolver.py)

`resolve(role: ModelType) -> list[ResolvedCandidate]`: 从 `role_bindings` 拉出该角色的候选按 priority 排序, join `services` 拿到 `base_url` 与解密后的 `api_key`, 组装成 `ResolvedCandidate` 列表返回。

被 `MultiForwarder` 用来构造内部 `Forwarder` 实例; 也被 forward.py 用来读取 `context_length` (给短期记忆装填算预算)。

---

## 5. CLI 命令

在 `mnemosync login` 进入交互式 shell 后可用:

| 命令 | 说明 |
|------|------|
| `ad-service` | 添加服务商 (交互输入 id / base_url / api_key) |
| `ls-service` | 列出服务商 (脱敏 API Key) |
| `show-service <id>` | 查看服务商详情 |
| `rm-service <id>` | 删除服务商 + 级联删除 role_bindings |
| `ls-models <id>` | 通过 Forwarder 拉取服务商 `/models` 端点 |
| `set-model <role> <service_id> <model> [--context N] [--dim N] [--send-dim]` | 追加角色绑定 (embedding 只允许一条; `--send-dim` 才透传 `dimensions` 上游) |
| `rm-model <role> <priority>` | 删除某个绑定 |
| `set-embedding-model <service_id> <model> --dim N` | 快捷设置嵌入 (替换现有) |
| `test-model <id> <model>` | 探活 |
| `memory reindex [--prune] [--threshold F]` | 换嵌入模型后走 Reindex (走 panel HTTP) |
| `memory prune [--threshold F] [--dry-run]` | 独立衰减清理 |

具体实现见 [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py)。

---

## 6. 与 Agent 的映射

| Agent | 使用角色 |
|-------|---------|
| 主对话 | MAIN |
| 记忆分析 | ASSIST (需支持 function_call) |
| 关系分析 | ASSIST |
| 代理思考 | ASSIST |
| 提示词清洗 (v0.2.1) | ASSIST |
| MemoryRetriever / MemoryLifecycle | EMBEDDING |
| MemoryRetriever 精排 | RERANK |

---

## 7. 错误

| 情境 | 处理 |
|------|------|
| 服务商 id 重复 | `save_service` 报错, CLI 提示重输 |
| 服务商不存在 | `add_role_binding` 校验失败 |
| 添加第二条 embedding 绑定 | `add_role_binding` raise ValueError; REST 端点转 409 |
| 对 embedding 角色 reorder | raise; REST 端点转 400 |
| API Key 解密失败 | `ValueError` (密钥损坏或数据被篡改) |
| 服务商不可达 | MultiForwarder 按候选优先级 fallback (embedding 除外直接抛) |

---

## 8. 模块结构

```
src/infra/llm_service/
├── __init__.py
├── models.py    # LLMServiceProvider / RoleBinding / ResolvedCandidate / ModelType
└── store.py     # LLMServiceStore (SQLite + Fernet)

src/core/models/
└── resolver.py  # RoleResolver: role_bindings + services → ResolvedCandidate
```

---

## 9. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [Forwarder / MultiForwarder](forward.md) | 消费 `ResolvedCandidate` 构造 Forwarder 实例, 按 priority fallback |
| [配置](../configuration.md) | `[chat]`/`[embedding]`/`[rerank]` 段已废弃; 模型绑定统一走本模块 |
| [记忆系统](memory-system.md) | 短期记忆装填用 `main_candidate.context_length`; 嵌入侧受锁保护 |
| [CLI](cli.md) | 交互命令的实现载体 |

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 初始: 服务商 + 模型配置 + Fernet 加密 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 模块路径 `src/infra/llm_service/`, 方法名 `save_service` / `delete_service`, 与 config.local.toml 的关系说明 |
| v0.2.3 | 2026-07-17 | 引入 `role_bindings` 表 + `ModelType.EMBEDDING/RERANK`; `[chat]/[embedding]/[rerank]` 段废弃; `RoleResolver` 组装 `ResolvedCandidate` |
| v0.2.4 | 2026-07-17 | 嵌入角色单绑定约束; `context_length` / `embedding_dim` 元数据字段; Reindex + Prune 触发点 |
| v0.2.8 | 2026-07-18 | `send_dimensions` 透传开关: 拆分向量库锁与上游 `dimensions` 参数, 默认不透传 (兼容 bge/bce/jina/mistral/gemini 等固定维模型) |
