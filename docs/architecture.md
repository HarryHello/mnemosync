# 架构设计文档 | Architecture Design

> **系统版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-24
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

**Mnemosync** 是一个基于 **LangGraph 多 Agent 编排** 的跨平台人格记忆管理系统, 位于前端客户端与模型服务商之间。核心价值不在于转发, 而在于让 AI 人格在不同接入平台之间保持统一的长期记忆。

### 1.1 定位

- **单人格多用户架构 (v0.3.0)**: 一个 Mnemosync 实例对应一个人格, 人格由服务器端权威定义 (不从客户端请求传入); 同时服务多个真实用户——API Key 绑定**身份识别策略** (direct / api_key_bound / regex / llm), 服务器侧从请求中识别参与者 (Actor), 记忆与关系按**有效用户 ID** (effective_user_id) 隔离。同一人在不同平台的身份可经用户组 (UserGroup) 归一。群聊按空间 (space) 分区, 记忆检索先按受众过滤再交给模型。详见 [modules/identity.md](modules/identity.md)。
- **非归属模式**: 未绑定策略或解析失败的请求不建立身份、不读写私有记忆, 仍可正常回复。不存在 v0.2.x 的 `"default"` 兜底用户。
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
| 服务器拥有身份 (v0.3.0) | 参与者身份由服务器按策略从请求中解析, 客户端不可声明/伪造; 记忆检索先按受众过滤再交给模型, 不靠 prompt 防泄露 |
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

一次请求最多 5 个 Agent (不含 Expressor), 默认路径激活 3 个 (代理思考/Expressor 默认关):

| # | Agent | 推理方法 | 触发时机 |
|---|-------|---------|---------|
| 1 | 主对话 | 直接推理 | 每次请求必跑 |
| 2 | 代理思考 | CoT (可选) | `proxy_thinking_enabled=True` 时, 在主对话前 |
| 3 | 记忆分析 | ReAct | 主对话后, 与关系分析并行 |
| 4 | 关系分析 | ReAct | 主对话后, 与记忆分析并行 |
| 3 | Expressor | ASSIST 调用 (可选) | 群聊非流式, 主对话后, 文本 > 10 字符 |

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

**6 个节点**, 无独立的 vector_index 节点——嵌入向量的写入在 `memory_analysis_node` 内由 `MemoryLifecycle.store_candidate()` 顺手完成。Expressor 在主对话后条件执行 (仅群聊非流式 stop 文本 > 10 字符), 不在拓扑图中单独节点。代码见 [src/core/graph/builder.py](../src/core/graph/builder.py)。

### 3.4 AgentState (共享状态)

真实定义见 [src/core/graph/state.py](../src/core/graph/state.py):

```python
class AgentState(TypedDict, total=False):
    # parse_request 写入 + API 层预注入
    messages: list[dict]
    extracted_new: list[dict]
    tools: list[dict] | None       # 客户端本轮工具定义, 仅 MAIN 可用
    tool_choice: str | dict | None
    parallel_tool_calls: bool | None
    tool_transaction: ToolTransactionTail | None  # 已校验的客户端工具续轮
    source_user: str                # v0.3.0: = effective_user_id (可为空 = 非归属)
    actor_id: str | None            # v0.3.0: 当前参与者
    persona: str
    persona_name: str
    persona_id: str                 # v0.3.0: 人格标识 (当前固定 "default", 从 state 读)
    thread_id: str
    proxy_thinking_enabled: bool
    space_id: str | None            # v0.3.0: 会话空间 (群聊分区)
    channel_type: str | None        # v0.3.0: "direct" | "group" | None
    current_speaker: str | None     # v0.3.0: 模型可读的当前发言者身份
    active_participants: list[str]  # v0.3.0: 裁剪后短期历史中的活跃参与者
    prompt_cleaning_result: dict | None  # v0.2.1: {clean_prompt, reasoning}

    # proxy_thinking 写入
    proxy_thinking_result: str | None

    # main_dialogue 写入
    main_model: str                 # v0.2.3: 由 RoleResolver 解析
    response: str                   # 最终用户可见文本; 纯工具调用时为空
    response_message: dict          # 完整 assistant message (可含 tool_calls)
    finish_reason: str | None       # stop / length / tool_calls / 上游扩展值
    response_chunks: list[bytes]
    upstream_usage: dict | None     # v0.2.5: 上游返回的 usage (tokens)
    emotion_analysis: dict          # v0.3.0: 情绪预计算, 两个分析 Agent 共享

    # memory_analysis 写入
    new_memories: list[dict]
    decay_evaluations: list[dict]   # v0.3.0: 恒空 (衰减改确定性公式)
    decay_targets: list[dict]

    # relationship_analysis 写入
    relationship_delta: dict

    # 全局
    errors: list[str]
    stream_mode: bool
```

**请求级附加键** (v0.3.0): forward.py 另注入 `source_frontend` / `external_event_id` / `api_key_id` 等不在 TypedDict 中的键 (LangGraph 容忍额外输入键), 供流水回写与幂等记录使用。

检索到的记忆 (`retrieved_memories` / `permanent_memories`) **不入 state** — 由 forward.py 或 `main_dialogue_node` 内部处理, 减少 checkpoint 体积。短期记忆的 `conversation_turns` 也不入 state, 装填时直接从 SqliteConversationStore 读, 装填完的 messages 才进 state。

---

## 4. 数据流时序

### 4.1 流式请求 (生产主路径)

```
Client ──► /v1/chat/completions (stream=true)
             │
             ▼
    [forward.py._handle_stream]
      0. _verify_api_key + 身份解析 _resolve_identity_context (v0.3.0)
         + 幂等预检 _lookup_idempotency (命中则重放首次响应, 零 LLM 开销)
         + _resolve_source_frontend + RoleResolver
      1. 加载永久记忆 + 语义检索 + 关系状态 (全程 RetrievalContext 受众过滤)
      2. render_main_dialogue_system() → system_text
      3. build_short_term_history(space_id=...) → 双窗裁剪, 群聊只读本空间 (v0.2.6/v0.3.0)
      4. build_main_dialogue_messages(system, history, new_user)
      5. Forwarder.chat_stream (MultiForwarder 按 main 候选优先级) → 上游 SSE
      6. 边收边 yield 给客户端 (零缓冲)
      7. 流结束:
         - conversation_store.append(user + assistant, actor_id/space_id/external_event_id) [v0.2.6/v0.3.0]
         - _record_idempotency (首次响应落缓存)
         - asyncio.create_task(_run_memory_graph)
             │
             ▼
    [后台记忆图] (不阻塞客户端)
      main_dialogue 结果已就绪 → 直接跑
        ├─ relationship_analysis (并行; 非归属模式跳过)
        └─ memory_analysis (并行; 非归属模式跳过)
              └─ MemoryLifecycle 写向量 + SQLite (受 embedding lock 保护, v0.2.4;
                 群聊记忆带 space_id 标记, v0.3.0)
```

### 4.2 非流式请求

`_handle_non_stream` → 直接 `graph.ainvoke(initial_state)` 跑完整个图, 记忆分析 / 关系分析同步完成后返回。

**关键约束**: 阶段 1 的检索必须低延迟 (本地 Chroma + 嵌入 API), 否则 TTFT 崩溃; 阶段 5 必须异步, 否则流式模式失去意义。

---

## 5. 记忆机制

### 5.1 短期记忆 (跨前端连续对话 + 空间事件流)

Mnemosync 的核心不变量: **同一个用户 (effective_user_id) 的多个前端汇聚为一条连续流**。AstrBot / AIRI / Web 面板 / 直接调 SDK 的脚本, 服务端把它们的对话汇聚成一条连续流, 装填时无视客户端携带的历史。v0.3.0 起群聊按 `space_id` 再分区: 群聊上下文只读本空间的流水。

- **存储**: `conversation_turns` 表 (id, role, content, ts, token_count, source_frontend, actor_id, space_id, external_event_id, committed_sequence, late_arrival), 单库 `data/conversation.db`
- **空间事件流 (v0.3.0)**: `space_id` 非空时同事务分配空间内单调序号 `committed_sequence` (MAX+1); 事件时间早于空间内最新已提交时间标记 `late_arrival`; `list_for_space()` 按序号定序读取
- **写入**: 主对话流结束时, `_handle_stream` / `_handle_non_stream` 各 append 一条 user turn + 一条 assistant turn; `source_frontend` 取 `api_key.note`, `actor_id` / `space_id` / `external_event_id` 来自身份解析 (服务器侧派生, 不信任客户端)
- **装填 (双窗口)**:
  - 时间窗: `settings.storage.short_term_days` (默认 7d) 硬边界
  - 模型窗: 用 `ResolvedCandidate.context_length` 算预算 `= ctx - system - new_user - reserve_output`, 从最老那端往新裁剪
  - 应答保留区: 客户端 `max_tokens` 优先; 否则 `min(4096, ctx/4)` 下限 512
  - 空间隔离 (v0.3.0): `space_id` 非空只读本空间候选
- **清理**: `lifespan` 起后台任务, 每 24h 删掉窗外记录
- **面板重置**: `DELETE /panel/admin/conversation-turns` 全清或按 `since` 部分清理; 前端 UI 的"清空对话"仅影响客户端展示, 不会抹掉服务器的连续记忆

设计动因见 [dev-decisions.md](dev-decisions.md) 决策 6 与 v0.3.0 决策。LangGraph 的 `MemorySaver` checkpoint 仍存在但已退化为"单请求内节点间共享 state", 不再承担跨请求短期记忆职责。

### 5.2 长期记忆

- 向量存储: ChromaDB (`hnsw:space=cosine`), 由 [src/infra/vector_store.py](../src/infra/vector_store.py) 封装
- 元数据存储: SQLite + aiosqlite, 见 [src/persistence/memory_store.py](../src/persistence/memory_store.py)
- 检索路径 (v0.3.0): query → 嵌入模型 → **ChromaDB `$or` 受众粗筛** (自己桶 / PUBLIC / 本空间) → **AudienceFilter 精筛** (关系门槛 / deny-allow 策略) → 重排模型精排 → top_k
- 隔离边界: `source_user` 列存 effective_user_id; 群聊诞生的记忆带 `space_id` 标记 (空间共享候选)

字段设计、衰减模型与受众规则全表详见 [modules/memory-system.md](modules/memory-system.md); 身份与空间模型见 [modules/identity.md](modules/identity.md)。

### 5.3 身份与幂等 (v0.3.0)

- **身份库**: `data/identity.db` — actors / user_groups / actor_group_memberships / identity_strategies 四表, 由 [src/persistence/identity_store.py](../src/persistence/identity_store.py) 管理
- **幂等库**: `data/idempotency.db` — 按 `(api_key.id, external_event_id)` 缓存首次响应, 平台重发零成本重放, 由 [src/persistence/idempotency_store.py](../src/persistence/idempotency_store.py) 管理

---

## 6. 工具

| 工具 | 工厂 | 使用者 |
|------|------|-------|
| `vector_search` | `make_vector_search_tool(retriever, retrieval_ctx)` | 记忆分析 (function_call, v0.3.0 带受众过滤) |
| `update_addressing` | `make_update_addressing_tool(memory_store, persona_id, user_id, actor_id)` | 关系分析 (v0.2.10) |

情绪分析不再是 ReAct 工具 — `main_dialogue_node` 预计算一次后经 state 共享给两个分析 Agent (v0.3.0)。`time_decay_calculator` (衰减改确定性公式) 与 `sentence_classifier` (提示词清洗改单次重写) 已于 v0.2.12 移除。

工具通过工厂函数注入依赖 (Forwarder / VectorStore / MemoryStore / 受众上下文), 见 [modules/tools.md](modules/tools.md)。

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

## 8. 目录结构 (v0.3.0)

v0.1 的 `src/modules/` / `src/accounts/` / `src/models/` / `src/storage/` 已删除, 新布局:

| 位置 | 内容 |
|------|------|
| `src/api/` | FastAPI 路由 + 中间件 + lifespan (含 conversation prune loop) |
| `src/api/routes/admin_debug.py` | v0.2.5 调试面板 SSE / session-key 端点 |
| `src/cli/` | CLI 与交互式 shell (v0.3.0: `identity_cmd.py` 身份命令组) |
| `src/core/agents/` | Agent 执行函数 + prompt builder + ReAct 循环 |
| `src/core/agents/prompts/defaults/` | Agent 提示词默认层 (随包发布) |
| `src/core/prompts/` | PromptStore + registry (两层提示词加载/校验/备份) |
| `src/core/graph/` | LangGraph builder / nodes / state |
| `src/core/identity/` | **v0.3.0**: 身份模型 (Actor/UserGroup/策略/IdentityContext) + IdentityResolver |
| `src/core/memory/` | 记忆模型、生命周期、上下文拼装、Reindex (v0.2.4)、short_term (v0.2.6)、**audience 受众过滤 (v0.3.0)** |
| `src/core/models/` | v0.2.3 RoleResolver (从 role_bindings + services 组合 ResolvedCandidate) |
| `src/core/config.py` | 配置加载 |
| `src/infra/forwarder/` | Forwarder + MultiForwarder + debug_hook |
| `src/infra/llm_service/` | LLM 服务商 + role_bindings 存储 |
| `src/infra/vector_store.py` | Chroma 封装 (含 embedding lock, v0.2.4; v0.3.0 复合 where 粗筛) |
| `src/infra/extraction.py` | 消息提取 (v0.3.0 起无主路径调用方, 保留导出) |
| `src/infra/debug_bus.py` / `debug_context.py` | v0.2.5 调试事件总线 + correlation_id 传播 |
| `src/infra/character_card.py` | v0.3.3 角色卡导入 (SillyTavern V1/V2) |
| `src/infra/space_lock.py` | v0.3.3 空间级串行锁 |
| `src/core/persona/definition.py` | v0.3.3 结构化人格定义 (PersonaDefinition) |
| `src/core/tools/internal_registry.py` | v0.3.3 内部 tool 注册表 (身份绑定等) |
| `src/core/tools/identity_binding.py` | v0.3.3 跨平台身份绑定内部 tool |
| `src/persistence/` | SQLite 存储 (memory / auth / api_key / conversation / http_log / **identity / idempotency / persona / lorebook / space_policy**, 后三者 v0.3.3) |
| `src/tools/` | Agent 工具工厂 |

数据文件: `data/` 下 memory.db / conversation.db / auth.db / api_keys.db / http_logs.db / llm_service.db / notifications.db, v0.3.0 新增 **identity.db** (身份四表) 与 **idempotency.db** (重放缓存)。备份需覆盖全部, 见 [deployment.md](deployment.md)。

---

## 9. 扩展点

1. **Agent 替换**: 每个 Agent 是独立执行函数, prompt 与模型可独立替换
2. **工具扩展**: 实现 `@tool` 装饰器 + 工厂函数, 注入依赖后注册到对应 node
3. **存储后端**: ChromaDB 可替换为 Milvus / Weaviate
4. **嵌入模型**: 可切换, 但需重新生成全量向量 (维度可能改变)
5. **多人格 (未来)**: 数据模型已预留 `persona_id`, 需配合 `personas` 表 + Admin API 实现服务器端人格存储 (多用户已在 v0.3.0 实现, 见 [modules/identity.md](modules/identity.md))
6. **人格自我演化 (远期)**: 模型根据对话历史自动更新服务器端 persona prompt, 让人格随时间"成长"

---

## 10. 约束

- 不训练模型
- 不绕过上游能力限制
- 不保证记忆 100% 永久保留 (受衰减策略与永久记忆限额约束)
- 单人格多用户 (v0.3.0): 记忆/关系按 effective_user_id 隔离, 群聊按 space 分区; 同一用户的跨前端会话在服务端合并为同一条流
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
| v0.3.0 | 2026-07-26 | **单人格多用户**: 身份模型 (Actor / UserGroup / effective_user_id) + 四种身份策略绑定 API Key + 非归属模式; 空间事件流 (space_id / committed_sequence / late_arrival / list_for_space); 幂等重放 (idempotency.db); 受众过滤检索 (AudienceFilter 两级过滤); 关系按 effective_user_id 分区; 移除全部 `"default"` 用户硬编码; 新增 `src/core/identity/`、identity_store / idempotency_store; 面板「身份管理」页 + `mnemosync identity` CLI |
| v0.3.3 | 2026-07-28 | **结构化人格 + 插件 + 工具协议**: PersonaDefinition 结构化人格定义 (身份/风格/空间覆盖); SillyTavern V1/V2 角色卡导入; 身份解析插件 (plugin 策略类型); 内部 tool 注册表 (InternalToolRegistry) + 跨平台身份绑定; Expressor 表达改写层; 空间级串行锁; SocialPolicy; Lorebook; persona_store / lorebook_store / space_policy_store |
| v0.3.4 | 2026-07-30 | **多人格 profile**: personas 表 + 切换 API; PersonaIdentity 移除 per-user 字段; 用户自助跨平台绑定; 人格改名 |
| v0.3.3 | 2026-07-28 | **工具协议完整闭环**: Expressor 表达改写层; 工具事务桥接 + 幂等重放; API Key 工具策略 (白名单/黑名单/冷却/全局频率); 工具参数隐私检查; 模型候选工具能力声明; 平台能力提示 + 选择性参与指南; 表达习惯学习; **调试与可观测性**: 管线事件 (6 类) + 前端渲染; 交互事务聚合; 评估维度统计; **并发与身份**: 空间级串行锁; 跨平台身份绑定 (指令 + 内部 tool); 内部 tool 注册表; **人格系统**: 结构化人格定义 (PersonaDefinition + SQLite 存储 + 版本化); 按空间覆盖表达倾向; 角色卡导入 (SillyTavern V1/V2); Lorebook 关键词匹配 + 注入; 记忆纠正 (supersede 软替代); SocialPolicy 空间社交策略 |
