# 架构设计文档 | Architecture Design

> **系统版本**: v0.2.11
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-19
> **作者**: HarryHelloo

---

## 1. 概述

**Mnemosync** 是一个基于 **LangGraph 多 Agent 编排** 的跨平台人格记忆管理系统, 位于前端客户端与模型服务商之间。核心价值不在于转发, 而在于让 AI 人格在不同接入平台之间保持统一的长期记忆。

### 1.1 定位

- **单人格架构**: 一个 Mnemosync 实例对应一个人格, 人格由服务器端权威定义 (不从客户端请求传入); 多个 API Key 用于区分前端来源 (AstrBot / AIRI / Web 等), 不做多用户隔离, 共享同一份记忆池。
- **中间件**: 本地不运行大模型, 所有 LLM 调用通过 Forwarder 转发到远端服务商。

### 1.2 核心价值

| 维度 | 传统方案 | Mnemosync |
|------|---------|-----------|
| 记忆存储 | 按平台/会话隔离 | 统一池 + 语义检索 |
| 记忆衰减 | 固定 TTL 或无衰减 | Agent 多维评估 |
| 前端适配 | 各端独立配置 | 统一人格 + OpenAI 兼容出口 |
| 去重 | MD5 精确匹配 | embedding 相似度 |

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| 服务器拥有人格 | 人格 prompt 由服务器端权威定义, 不从客户端请求提取; 客户端 system 消息中的人格描述由提示词清洗 Agent 剥离 (见 [modules/agents.md §6](modules/agents.md#6-提示词清洗-agent)), 仅保留功能性指令合并注入 |
| 提示词可自定义 | 所有 Agent 提示词从硬编码常量迁到两层 Markdown 文件系统 (defaults + 用户覆盖), 高级用户可通过 CLI 或 REST 面板调整而不改代码/不重启, 详见 [modules/agents.md §7](modules/agents.md#7-自定义-agent-提示词) |
| Agent 驱动决策 | 记忆的提取/衰减/关系变化由 Agent 智能判断, 非硬编码 |
| 预处理优先 | 记忆加载与上下文拼装必须在转发到上游模型**之前**完成 |
| 兼容即插即用 | 严格遵循 OpenAI `/v1/chat/completions` 规范 |
| 流式透传 | SSE 零缓冲透传, TTFT 不受影响 |
| 软性遗忘 | 衰减只降低优先级, 不物理删除 |
| 永久记忆限额 | 上限 15 条防止上下文稀释 |
| 三维分离 | importance / decay_rate / expires_at 分开建模 |

---

## 3. 系统架构

### 3.1 分层职责

| 层 | 位置 | 职责 |
|----|------|------|
| **编排层** | 本地 LangGraph | 节点调度、并行分支、条件路由、共享 state |
| **组装层** | 本地 Agent 节点 | 拼 prompt、序列化 tools schema、解析 function_call |
| **传输层** | 本地 Forwarder | 唯一 HTTP 出口, 连接池, 超时, SSE |
| **推理层** | 远端模型服务商 | 实际执行 Think/Act/Observe 的"Think" |

Forwarder ([src/infra/forwarder/](../src/infra/forwarder/)) 不属于任何单个 Agent, 是所有 LLM 调用的共用通道; 具体见 [modules/forward.md](modules/forward.md)。

### 3.2 Agent 一览

一次请求最多 4 个 Agent, 默认路径激活 3 个 (代理思考默认关):

| # | Agent | 推理方法 | 触发时机 |
|---|-------|---------|---------|
| 1 | 主对话 | 直接推理 | 每次请求必跑 |
| 2 | 代理思考 | CoT (可选) | `proxy_thinking_enabled=True` 时, 在主对话前 |
| 3 | 记忆分析 | ReAct | 主对话后, 与关系分析并行 |
| 4 | 关系分析 | ReAct | 主对话后, 与记忆分析并行 |

详细规格见 [modules/agents.md](modules/agents.md)。

嵌入模型 / 重排模型是**基础设施工具**, 不算 Agent。

### 3.3 LangGraph 拓扑

```
parse_request (纯预处理节点)
      │
      ├─ proxy_thinking_enabled? ──► proxy_thinking
      │                                   │
      └───────────────────────────────► main_dialogue
                                            │
                              ┌─────────────┴─────────────┐
                              ▼                           ▼
                    relationship_analysis          memory_analysis
                              │                           │
                              └─────────────┬─────────────┘
                                            ▼
                                           END
```

**5 个节点**, 无独立的 vector_index 节点——嵌入向量的写入在 `memory_analysis_node` 内由 `MemoryLifecycle.store_candidate()` 顺手完成。代码见 [src/core/graph/builder.py](../src/core/graph/builder.py)。

### 3.4 AgentState (共享状态)

真实定义见 [src/core/graph/state.py](../src/core/graph/state.py):

```python
class AgentState(TypedDict, total=False):
    # parse_request 写入
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str
    source_frontend: str | None    # v0.2.5: 派生自 api_key.note
    persona: str
    persona_name: str
    thread_id: str
    proxy_thinking_enabled: bool
    prompt_cleaning_result: dict | None  # v0.2.1: 客户端 system 剥离结果

    # proxy_thinking 写入
    proxy_thinking_result: str | None

    # main_dialogue 写入
    main_model: str                 # v0.2.3: 由 RoleResolver 解析
    response: str
    response_chunks: list[bytes]
    upstream_usage: dict | None     # v0.2.5: 上游返回的 usage (tokens)

    # memory_analysis 写入
    new_memories: list[dict]
    decay_evaluations: list[dict]
    decay_targets: list[dict]

    # relationship_analysis 写入
    relationship_delta: dict

    # 全局
    errors: list[str]
    stream_mode: bool
```

检索到的记忆 (`retrieved_memories` / `permanent_memories`) **不入 state** — 由 forward.py 或 `main_dialogue_node` 内部处理, 减少 checkpoint 体积。短期记忆的 `conversation_turns` 也不入 state, 装填时直接从 SqliteConversationStore 读, 装填完的 messages 才进 state。

---

## 4. 数据流时序

### 4.1 流式请求 (生产主路径)

```
Client ──► /v1/chat/completions (stream=true)
             │
             ▼
    [forward.py._handle_stream]
      0. _verify_api_key + _resolve_source_frontend + RoleResolver
      1. 加载永久记忆 + 语义检索 + 关系状态
      2. render_main_dialogue_system() → system_text
      3. build_short_term_history() → 双窗裁剪 conversation_turns (v0.2.6)
      4. build_main_dialogue_messages(system, history, new_user)
      5. Forwarder.chat_stream (MultiForwarder 按 main 候选优先级) → 上游 SSE
      6. 边收边 yield 给客户端 (零缓冲)
      7. 流结束:
         - conversation_store.append(user + assistant) [v0.2.6]
         - asyncio.create_task(_run_memory_graph)
             │
             ▼
    [后台记忆图] (不阻塞客户端)
      main_dialogue 结果已就绪 → 直接跑
        ├─ relationship_analysis (并行)
        └─ memory_analysis (并行)
              └─ MemoryLifecycle 写向量 + SQLite (受 embedding lock 保护, v0.2.4)
```

### 4.2 非流式请求

`_handle_non_stream` → 直接 `graph.ainvoke(initial_state)` 跑完整个图, 记忆分析 / 关系分析同步完成后返回。

**关键约束**: 阶段 1 的检索必须低延迟 (本地 Chroma + 嵌入 API), 否则 TTFT 崩溃; 阶段 5 必须异步, 否则流式模式失去意义。

---

## 5. 记忆机制

### 5.1 短期记忆 (跨前端连续对话)

Mnemosync 的核心不变量: **多个前端 = 同一个用户**。AstrBot / AIRI / Web 面板 / 直接调 SDK 的脚本, 服务端把它们的对话汇聚成一条连续流, 装填时无视客户端携带的历史。

- **存储**: `conversation_turns` 表 (id, role, content, ts, token_count, source_frontend), 单库 `data/conversation.db`, 无 thread/user 分区
- **写入**: 主对话流结束时, `_handle_stream` / `_handle_non_stream` 各 append 一条 user turn + 一条 assistant turn; `source_frontend` 取 `api_key.note` (服务器侧派生, 不信任客户端)
- **装填 (双窗口)**:
  - 时间窗: `settings.storage.short_term_days` (默认 7d) 硬边界
  - 模型窗: 用 `ResolvedCandidate.context_length` 算预算 `= ctx - system - new_user - reserve_output`, 从最老那端往新裁剪
  - 应答保留区: 客户端 `max_tokens` 优先; 否则 `min(4096, ctx/4)` 下限 512
- **清理**: `lifespan` 起后台任务, 每 24h 删掉窗外记录
- **面板重置**: `DELETE /panel/admin/conversation-turns` 全清或按 `since` 部分清理; 前端 UI 的"清空对话"仅影响客户端展示, 不会抹掉服务器的连续记忆

设计动因见 [dev-decisions.md](dev-decisions.md) 决策 6。LangGraph 的 `MemorySaver` checkpoint 仍存在但已退化为"单请求内节点间共享 state", 不再承担跨请求短期记忆职责。

### 5.2 长期记忆

- 向量存储: ChromaDB (`hnsw:space=cosine`), 由 [src/infra/vector_store.py](../src/infra/vector_store.py) 封装
- 元数据存储: SQLite + aiosqlite, 见 [src/persistence/memory_store.py](../src/persistence/memory_store.py)
- 检索路径: query → 嵌入模型 → cosine 粗筛 → 重排模型精排 → top_k

字段设计与衰减模型详见 [modules/memory-system.md](modules/memory-system.md)。

---

## 6. 工具

| 工具 | 工厂 | 使用者 |
|------|------|-------|
| `vector_search` | `make_vector_search_tool` | 记忆分析 (function_call) |
| `emotion_analyzer` | `make_emotion_analyzer_tool` | 记忆分析、关系分析 |
| `time_decay_calculator` | `make_time_decay_calculator_tool` | 记忆分析 |

工具通过工厂函数注入依赖 (Forwarder / VectorStore / MemoryStore), 见 [modules/tools.md](modules/tools.md)。

---

## 7. 技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| Agent 编排 | LangGraph + LangChain | StateGraph、tools 协议 |
| 主模型 | 大参数对话模型 | 生成回复 |
| 辅助模型 | 支持 function_call 的轻量模型 | 记忆/关系/代理思考 Agent |
| 嵌入模型 | 服务商 API | 文本 → 向量 |
| 重排模型 | 服务商 API | 检索精排 |
| 向量存储 | ChromaDB | 本地嵌入式 |
| 元数据存储 | SQLite + aiosqlite | 记忆/关系/API Key |
| API | FastAPI | OpenAI 兼容 |
| HTTP | httpx | Forwarder |
| Python | ≥ 3.12 | |

具体模型由 `config.local.toml` 配置, 不绑定服务商。嵌入维度由所选模型决定 (见 [dev-decisions.md](dev-decisions.md) 决策 3)。

---

## 8. 目录结构 (v0.2.6)

v0.1 的 `src/modules/` / `src/accounts/` / `src/models/` / `src/storage/` 已删除, 新布局:

| 位置 | 内容 |
|------|------|
| `src/api/` | FastAPI 路由 + 中间件 + lifespan (含 conversation prune loop) |
| `src/api/routes/admin_debug.py` | v0.2.5 调试面板 SSE / session-key 端点 |
| `src/cli/` | CLI 与交互式 shell |
| `src/core/agents/` | Agent 执行函数 + prompt builder + ReAct 循环 |
| `src/core/agents/prompts/defaults/` | Agent 提示词默认层 (随包发布) |
| `src/core/prompts/` | PromptStore + registry (两层提示词加载/校验/备份) |
| `src/core/graph/` | LangGraph builder / nodes / state |
| `src/core/memory/` | 记忆模型、生命周期、上下文拼装、Reindex (v0.2.4)、short_term (v0.2.6) |
| `src/core/models/` | v0.2.3 RoleResolver (从 role_bindings + services 组合 ResolvedCandidate) |
| `src/core/config.py` | 配置加载 |
| `src/infra/forwarder/` | Forwarder + MultiForwarder + debug_hook |
| `src/infra/llm_service/` | LLM 服务商 + role_bindings 存储 |
| `src/infra/vector_store.py` | Chroma 封装 (含 embedding lock, v0.2.4) |
| `src/infra/extraction.py` | 消息提取 (仅供后台记忆图) |
| `src/infra/debug_bus.py` / `debug_context.py` | v0.2.5 调试事件总线 + correlation_id 传播 |
| `src/persistence/` | SQLite 存储 (memory / auth / api_key / conversation / http_log) |
| `src/tools/` | Agent 工具工厂 |

---

## 9. 扩展点

1. **Agent 替换**: 每个 Agent 是独立执行函数, prompt 与模型可独立替换
2. **工具扩展**: 实现 `@tool` 装饰器 + 工厂函数, 注入依赖后注册到对应 node
3. **存储后端**: ChromaDB 可替换为 Milvus / Weaviate
4. **嵌入模型**: 可切换, 但需重新生成全量向量 (维度可能改变)
5. **多人格 (未来)**: 数据模型已预留 `persona_id`, 需配合 `personas` 表 + Admin API 实现服务器端人格存储
6. **人格自我演化 (远期)**: 模型根据对话历史自动更新服务器端 persona prompt, 让人格随时间"成长"

---

## 10. 约束

- 不训练模型
- 不绕过上游能力限制
- 不保证记忆 100% 永久保留 (受衰减策略与永久记忆限额约束)
- 当前单人格单用户 (v0.2.x); 跨前端会话在服务端合并为同一条流
- **不能修改客户端行为**: 中间件功能不得依赖任何客户端配合 (自定义 header、UI 语义、清空按钮语义等)

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-29 | 初始确定性管道 |
| v0.2.0 | 2026-07-12 | 重构为 LangGraph 多 Agent; ChromaDB; 代理思考 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 修正拓扑 (5 节点, 无 vector_index)、AgentState 字段、目录结构 |
| v0.2.1 | 2026-07-16 | 明确服务器优先人格设计原则: 人格由服务器端权威定义, 不从客户端请求提取; 新增人格自我演化远期规划 |
| v0.2.1 | 2026-07-16 | 新增"提示词可自定义"核心决策; 提示词从 Python 常量迁到 defaults + 覆盖两层文件系统; admin 路由统一鉴权 |
| v0.2.3 | 2026-07-17 | 模型绑定从 config 迁到 `role_bindings` 表; 引入 RoleResolver + MultiForwarder 多候选 fallback; main_model/source_frontend/upstream_usage/prompt_cleaning_result 进 AgentState |
| v0.2.4 | 2026-07-17 | 嵌入角色单绑定 + Chroma collection 锁 (service_id/model/dim); Reindex + Prune 端点 |
| v0.2.5 | 2026-07-17 | 调试面板 (DebugEventBus + SSE + panel-debug API key + use_agent 标签点) |
| v0.2.6 | 2026-07-18 | 短期记忆重定义: 服务器维护跨前端 `conversation_turns` 流, 时间窗 (默认 7d) + 模型窗双窗装填, 忽略客户端携带的历史 |
| v0.2.7 | 2026-07-18 | 新增 `POST /panel/admin/persona/reset`: 原子清空 memory_entries (含 PERMANENT) / relationships / conversation_turns / Chroma collection, 保留服务商 / API Key / 提示词覆盖 |
| v0.2.8 | 2026-07-18 | CLI `--debug` 模式: 全链路请求/响应落库到 `data/http_logs.db`, 面板 `请求日志` 支持按 note / status / 时间筛选 |
| v0.2.9 | 2026-07-18 | 默认人格改为"宅家内向的妹妹"; TOML `[persona.relation]` 抽出 `persona_addressing / user_addressing / context` 三字段, 记忆分析 / 关系分析 prompt 通过占位符消费 |
| v0.2.10 | 2026-07-19 | 关系称呼动态演化: `relationships` 加 3 个 nullable 列 + `relationship_audit_log` 表; 关系分析 Agent 获 `update_addressing` 工具; 面板 `RelationshipsPage` 加编辑对话框 + 变更历史 + 回退按钮 |
| v0.2.11 | 2026-07-19 | 人格面板编辑: `GET/PUT/DELETE /panel/admin/persona` + `data/persona_override.toml` (优先级最高); 面板 `MemoriesPage` 全列 sortable + filter (含 `source_frontend` 枚举筛选); 亲密度 / 信任度进度条按数值分档着色; 全局品牌图标改为 SVG favicon; 文档批量对齐 |
