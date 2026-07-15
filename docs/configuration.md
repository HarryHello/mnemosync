# 配置文档 | Configuration

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 用**单一配置源**: 项目根目录下的 `config.local.toml`。文件不存在则启动直接报错 (见 [config.py:135](../src/core/config.py#L135))。所有模型凭证、模型选型、存储路径、运行参数都在这里配置。

**辅助入口**:
- 首次部署: 复制 `config.example.toml` → `config.local.toml`, 填入真实凭证
- 环境变量仅接管极少数运行时项 (见 [§4](#4-环境变量-有限支持))
- 嵌入模型可通过 CLI 交互命令 `set-embedding-model` 更新, CLI 会用 `config_writer` 改回 `config.local.toml`

---

## 2. 配置文件结构

真实定义见 [src/core/config.py](../src/core/config.py):

| 段 | 必需 | 说明 |
|----|------|------|
| `[chat]` | 是 | 对话模型 (主/辅助共用一个服务商) |
| `[embedding]` | 是 | 嵌入模型 |
| `[rerank]` | 否 | 重排序模型; 缺省时降级为纯 cosine 检索 |
| `[storage]` | 否 | 数据库/向量库路径 |
| `[memory]` | 否 | 记忆系统参数 |
| `[graph]` | 否 | LangGraph 编排参数 |
| `[runtime]` | 否 | HOST/PORT/log_level |

---

## 3. 各段字段

### 3.1 [chat]

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_url` | str | 服务商 OpenAI 兼容基址 |
| `api_key` | str | 服务商 API Key |
| `main_model` | str | 主对话 Agent 用的模型 |
| `assist_model` | str | 记忆/关系/代理思考 Agent 用的模型 (需支持 function_call) |

```toml
[chat]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key  = "sk-xxx"
main_model   = "qwen-max"
assist_model = "qwen-turbo"
```

### 3.2 [embedding]

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_url` | str | 嵌入服务基址 |
| `api_key` | str | 服务商 API Key |
| `model` | str | 嵌入模型名 |
| `dimensions` | int | 可选; 部分服务商需要显式指定维度 |

**维度由模型决定, 不再写死**——例如 DashScope `text-embedding-v3` 支持 1024/768/512/256/128/64。切换嵌入模型必须同时: (1) 更新 `model`; (2) 若必要更新 `dimensions`; (3) **重新生成 ChromaDB 全量向量** (旧向量维度不匹配)。参见 [dev-decisions.md](dev-decisions.md) 决策 3。

### 3.3 [rerank]

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_url` | str | 重排端点基址 (可能与 chat 不同) |
| `api_key` | str | 服务商 API Key |
| `model` | str | 重排模型名 |

**注意**: DashScope 的 rerank 端点是 `/compatible-api/v1` (不是 `/compatible-mode/v1`)。且 `gte-rerank` 已于 2026-05-30 下线, 请用 `qwen3-rerank` 或 `gte-rerank-v2`。

```toml
[rerank]
base_url = "https://dashscope.aliyuncs.com/compatible-api/v1"
api_key  = "sk-xxx"
model    = "qwen3-rerank"
```

整段注释掉即禁用重排。

### 3.4 [storage]

| 字段 | 默认 | 说明 |
|------|------|------|
| `memory_db_path` | `data/memory.db` | 记忆元数据 SQLite |
| `llm_db_path` | `data/llm_service.db` | LLM 服务商配置 SQLite (可选功能) |
| `auth_db_path` | `data/auth.db` | 管理员账号 |
| `chroma_dir` | `data/chroma` | ChromaDB 持久化目录 |

路径相对项目根目录。API Key 数据库路径当前**在代码中硬编码**为 `data/api_keys.db` ([api_key.py:17](../src/api/routes/api_key.py#L17)), 不受 storage 段控制。

### 3.5 [memory]

| 字段 | 默认 | 说明 |
|------|------|------|
| `permanent_limit` | 15 | 永久记忆条数上限 |
| `permanent_load_top` | 7 | 主对话每次加载的永久记忆条数 |
| `retrieval_top_k` | 5 | 语义检索返回条数 |
| `decay_batch_size` | 50 | 衰减评估的批次大小 |

### 3.6 [graph]

| 字段 | 默认 | 说明 |
|------|------|------|
| `checkpoint_backend` | `memory` | LangGraph checkpoint 后端 (`memory` / `sqlite`) |
| `proxy_thinking_default` | false | 代理推理的**兜底**开关: 请求无 `reasoning_effort` 等提示、主模型也没原生推理时, 是否强制启用 |
| `proxy_thinking_native_reasoning_models` | 见下 | 视为具备原生推理的模型前缀白名单 (命中即 skip 代理推理) |

**默认前缀白名单**:
```
["o1*", "o3*", "o4*",
 "deepseek-r1*", "deepseek-reasoner*",
 "qwen3-*-thinking", "qwq*",
 "gpt-5-thinking-*"]
```
末尾 `*` 通配, 例如 `deepseek-r1*` 匹配 `deepseek-r1-distill-llama-70b`。除静态前缀外, 流式路径会自适应观察: 上游 chunk 出现 `reasoning_content` 字段 → 该模型加入进程内 `_native_cache`, 下次自动跳过 (重启清空)。

**代理推理决策规则**: 由 [src/api/reasoning_control.py](../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 判定, 优先级 tools → 原生识别 → 前台点名推理 → `proxy_thinking_default`。详见 [agents.md §4](modules/agents.md)。

### 3.7 [runtime]

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` | `0.0.0.0` | 监听地址 |
| `port` | 16125 | 监听端口 |
| `log_level` | `info` | 日志级别 |

---

## 4. 环境变量 (有限支持)

绝大多数配置只从 `config.local.toml` 读取。当前从环境变量读取的项:

| 变量 | 作用 |
|------|------|
| `HOST` / `PORT` | CLI `serve` 命令行标志覆盖 [runtime] |
| `MNEMOSYNC_DEBUG=1` | 打开 Forwarder 的上游请求/响应日志 |

无 `.env` 支持——不要指望通过 `.env` 或 `MEMORY_DB_PATH` 之类的环境变量覆盖 config 段。

---

## 5. 完整示例

```toml
[chat]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key  = "sk-xxx"
main_model   = "qwen-max"
assist_model = "qwen-turbo"

[embedding]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key  = "sk-xxx"
model    = "text-embedding-v3"
dimensions = 1024

[rerank]
base_url = "https://dashscope.aliyuncs.com/compatible-api/v1"
api_key  = "sk-xxx"
model    = "qwen3-rerank"

[storage]
memory_db_path = "data/memory.db"
chroma_dir     = "data/chroma"

[memory]
permanent_limit    = 15
permanent_load_top = 7
retrieval_top_k    = 5

[graph]
checkpoint_backend = "memory"
proxy_thinking_default = false
proxy_thinking_native_reasoning_models = [
  "o1*", "o3*", "o4*",
  "deepseek-r1*", "deepseek-reasoner*",
  "qwen3-*-thinking", "qwq*",
  "gpt-5-thinking-*",
]

[runtime]
host = "0.0.0.0"
port = 16125
log_level = "info"
```

---

## 6. 加载流程

1. `get_settings()` 首次调用触发 `load_settings()`
2. 检查 `config.local.toml` 存在性——否则抛 `FileNotFoundError`
3. 校验 `[chat]` / `[embedding]` 段必需字段, 缺任一段抛 `ValueError`
4. 可选段缺省时用默认值
5. 缓存单例, 之后调用返回同一实例
6. 测试可用 `_reset_settings()` 清缓存

---

## 7. 更换嵌入模型的完整步骤

1. 编辑 `config.local.toml` 的 `[embedding].model` 与 `dimensions`
2. 停服务
3. 清空 ChromaDB 目录 (`data/chroma`) 或用新目录
4. 重启服务; 已有 SQLite 记忆条目会在下次访问时按新维度重新生成向量, 或运行迁移脚本 (若有)

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 从环境变量为主改为 `config.local.toml` 单一配置源 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 移除虚构环境变量表, 嵌入维度由模型决定不再写死, 代理思考启用方式修正 |
