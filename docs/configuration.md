# 配置文档 | Configuration

> **系统版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

Mnemosync 的配置分为三类：

| 配置类型 | 配置方式 | 说明 |
|----------|----------|------|
| **运行时配置** | 环境变量 / `.env` 文件 | 服务端口、数据库路径、日志级别等 |
| **模型配置** | CLI 命令（持久化到数据库） | 服务商、主模型、辅助模型、嵌入/重排序模型 |
| **记忆系统参数** | 环境变量（带默认值） | 永久记忆限额、衰减阈值、批量大小等 |

> **设计原则**：模型选型不写死在代码里，由 CLI 命令动态配置并持久化。切换模型/服务商时无需重启或改代码，详见 [LLM 服务管理](modules/llm-service.md)。

---

## 2. 运行时配置（环境变量）

### 2.1 服务基础配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `16125` | 服务端口 |
| `LOG_LEVEL` | `info` | 日志级别（debug/info/warning/error） |
| `MNEMOSYNC_DB_PATH` | `data/api_keys.db` | API Key 数据库路径 |
| `AUTH_DB_PATH` | `data/auth.db` | 管理员认证数据库路径 |
| `LLM_SERVICE_DB_PATH` | `data/llm_service.db` | LLM 服务商配置数据库路径 |
| `MEMORY_DB_PATH` | `data/memory.db` | 记忆元数据库路径 |

### 2.2 模型服务商凭证

> ⚠️ v0.2.0 推荐通过 CLI 命令配置（`ad-service`），凭证加密存入数据库。
> 环境变量方式保留用于首次初始化或 CI 环境。

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DASHSCOPE_API_KEY` | — | DashScope API Key（推荐服务商） |
| `UPSTREAM_API_KEY` | — | 通用上游服务商 API Key（兼容 OpenAI 协议） |

### 2.3 `.env` 文件示例

```bash
# .env
HOST=0.0.0.0
PORT=16125
LOG_LEVEL=info

# 数据库路径
LLM_SERVICE_DB_PATH=data/llm_service.db
MEMORY_DB_PATH=data/memory.db

# 首次初始化用（之后由 CLI 管理）
DASHSCOPE_API_KEY=sk-your-dashscope-key
```

---

## 3. 模型配置（CLI 管理）

模型配置通过 CLI 命令持久化到数据库，详见 [LLM 服务管理文档](modules/llm-service.md)。

### 3.1 配置项概览

| 配置项 | CLI 命令 | 说明 |
|--------|----------|------|
| 添加服务商 | `ad-service` | 注册一个模型服务商（base_url + api_key） |
| 列出服务商 | `ls-service` | 查看已注册的服务商 |
| 列出可用模型 | `ls-models [srv_id]` | 从服务商拉取可用模型列表 |
| 设置主模型 | `set-main-model [srv_id] [model]` | 配置主对话 Agent 使用的模型 |
| 设置辅助模型 | `set-assist-model [srv_id] [model]` | 配置记忆分析/关系分析/代理思考用的模型 |
| 测试模型连接 | `test-model [srv_id] [model]` | 验证模型可达性 |
| 移除服务商 | `rm-service [srv_id]` | 删除服务商及其模型配置 |

### 3.2 模型角色与能力要求

| 角色 | 用途 | 能力要求 |
|------|------|----------|
| **主模型** | 主对话 Agent 生成回复 | 高质量对话生成 |
| **辅助模型** | 记忆分析、关系分析、代理思考 Agent | 低成本推理 + function_call 支持 |
| **嵌入模型** | 向量检索 Agent 的语义检索 | 文本向量化（与服务商绑定，非独立配置） |
| **重排序模型** | 向量检索 Agent 的候选精排 | 候选列表精排（与服务商绑定） |

> 嵌入模型和重排序模型由所选服务商提供，通常通过服务商的 embedding/rerank API 端点调用，使用服务商默认模型即可，无需单独配置。

### 3.3 典型配置流程

```bash
# 1. 添加 DashScope 服务商
mnemosync > ad-service
Custom service id: dashscope
base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
API key: *******************

# 2. 列出可用模型
mnemosync > ls-models dashscope
qwen-max
qwen-turbo
text-embedding-v3
gte-rerank
...

# 3. 设置主模型和辅助模型
mnemosync > set-main-model dashscope qwen-max
mnemosync > set-assist-model dashscope qwen-turbo

# 4. 测试连接
mnemosync > test-model dashscope qwen-max
✓ Connection successful.
```

---

## 4. 记忆系统参数

### 4.1 记忆分类与限额

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PERMANENT_MEMORY_LIMIT` | `15` | 永久记忆条数上限 |
| `PERMANENT_LOAD_TOP` | `7` | 主对话加载的永久记忆条数 |
| `RETRIEVAL_TOP_K` | `5` | 语义检索返回的记忆条数 |

### 4.2 衰减模型参数

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ACTIVE_THRESHOLD` | `0.3` | 优先级 > 此值 → ACTIVE（出现在上下文） |
| `DORMANT_THRESHOLD` | `0.1` | 优先级 > 此值 → DORMANT（检索可召回） |
| `WEAK_THRESHOLD` | `0.05` | 优先级 > 此值 → WEAK（高相似度可召回） |
| `FORGET_THRESHOLD` | `0.05` | 优先级 ≤ 此值 → FORGOTTEN |
| `ACCESS_BONUS_FACTOR` | `0.05` | 访问加成系数：log(访问次数+1) × 此值 |
| `DECAY_BATCH_SIZE` | `50` | 衰减 Agent 每次评估的记忆条数 |
| `DECAY_SKIP_HOURS` | `24` | 新建记忆多少小时内跳过衰减评估 |

### 4.3 衰减定时任务

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DECAY_CRON` | `0 3 * * *` | 定期衰减任务 cron 表达式（默认每天凌晨 3 点） |

---

## 5. 向量存储配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `CHROMA_PERSIST_DIR` | `./data/chroma` | ChromaDB 持久化目录 |
| `CHROMA_COLLECTION_NAME` | `mnemosync_memories` | ChromaDB collection 名称 |
| `CHROMA_DISTANCE` | `cosine` | 相似度计算方式（cosine / l2 / ip） |
| `EMBEDDING_DIM` | `768` | 嵌入向量维度（需与嵌入模型匹配） |

> ⚠️ `EMBEDDING_DIM` 必须与所选嵌入模型的输出维度一致。切换嵌入模型后需重新生成全量向量。

---

## 6. Agent 编排配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `CHECKPOINT_BACKEND` | `memory` | LangGraph checkpoint 后端（memory / sqlite） |
| `LANGGRAPH_THREADS_MAX` | `100` | 最大并发会话线程数 |
| `PROXY_THINKING_DEFAULT` | `false` | 代理思考模式默认是否启用（可被请求头覆盖） |

### 6.1 代理思考模式

代理思考 Agent 默认关闭。两种启用方式：

1. **请求级别**（优先级高）：在请求头中设置 `X-Enable-Proxy-Thinking: true`
2. **全局默认**：设置环境变量 `PROXY_THINKING_DEFAULT=true`

> 代理思考模式会增加约 200-500ms 延迟（额外一轮辅助模型推理），用于在弱推理模型上获得更好的回复质量。详见 [Agent 设计文档 §4](modules/agents.md#4-agent-3-代理思考-agent-proxy-thinking-agent)。

---

## 7. 转发器配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `FORWARDER_TIMEOUT` | `30.0` | 请求超时（秒） |
| `FORWARDER_CONNECT_TIMEOUT` | `10.0` | 连接超时（秒） |
| `FORWARDER_MAX_CONNECTIONS` | `50` | 最大连接数 |
| `FORWARDER_MAX_KEEPALIVE` | `10` | 最大保活连接数 |

---

## 8. 配置优先级

当同一配置项有多种来源时，优先级从高到低：

```
1. 请求头（如 X-Enable-Proxy-Thinking）   ← 仅限请求级配置
2. 环境变量 / .env 文件
3. 数据库持久化配置（CLI 命令设置）
4. 代码默认值
```

---

## 9. 配置校验

启动时会校验以下配置，不满足则拒绝启动：

| 校验项 | 失败原因 |
|--------|----------|
| 至少配置一个服务商 | 无法调用任何模型 |
| 已设置主模型 | 主对话 Agent 无法工作 |
| 已设置辅助模型 | 记忆分析等 Agent 无法工作 |
| `EMBEDDING_DIM` 与嵌入模型匹配 | 向量检索维度不一致 |
| 数据库目录可写 | 无法持久化 |

---

## 10. 完整配置示例

```bash
# === 基础 ===
HOST=0.0.0.0
PORT=16125
LOG_LEVEL=info

# === 数据库 ===
LLM_SERVICE_DB_PATH=data/llm_service.db
MEMORY_DB_PATH=data/memory.db
MNEMOSYNC_DB_PATH=data/api_keys.db
AUTH_DB_PATH=data/auth.db

# === 记忆系统 ===
PERMANENT_MEMORY_LIMIT=15
PERMANENT_LOAD_TOP=7
RETRIEVAL_TOP_K=5
ACTIVE_THRESHOLD=0.3
FORGET_THRESHOLD=0.05
DECAY_BATCH_SIZE=50
DECAY_CRON=0 3 * * *

# === 向量存储 ===
CHROMA_PERSIST_DIR=./data/chroma
CHROMA_COLLECTION_NAME=mnemosync_memories
CHROMA_DISTANCE=cosine
EMBEDDING_DIM=768

# === Agent 编排 ===
CHECKPOINT_BACKEND=memory
LANGGRAPH_THREADS_MAX=100
PROXY_THINKING_DEFAULT=false

# === 转发器 ===
FORWARDER_TIMEOUT=30.0
FORWARDER_MAX_CONNECTIONS=50
```

---

## 11. 相关文档

- [LLM 服务管理](modules/llm-service.md) — 服务商和模型的 CLI 配置
- [架构设计](architecture.md) — 系统整体架构
- [部署指南](deployment.md) — 部署方式与环境
- [Agent 设计](modules/agents.md) — 各 Agent 的模型使用

---

## 12. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.0.0 | 2026-03-24 | 初始占位（暂无实现） |
| v0.2.0 | 2026-07-12 | 完整配置文档：运行时配置、模型配置、记忆参数、向量存储、Agent 编排、转发器 |

---

> **维护者提示**:
> - 模型选型通过 CLI 动态配置，不要在代码里硬编码模型名。
> - 切换嵌入模型后必须重新生成 ChromaDB 全量向量，`EMBEDDING_DIM` 需同步更新。
> - 衰减阈值参数需要实际测试调优，默认值是理论值。