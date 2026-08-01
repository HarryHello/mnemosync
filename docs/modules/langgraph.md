# LangGraph 编排模块 | LangGraph Orchestration

> **模块版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-12
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

LangGraph 是 Mnemosync 的编排骨架。它决定节点执行顺序、在 Agent 间传递 state、驱动 ReAct 循环。它自身不做模型推理——所有 LLM 调用通过 [Forwarder](forward.md) 转发到远端服务商。

**代码入口**: [src/core/graph/](../../src/core/graph/) (`builder.py` / `nodes.py` / `state.py`)。

---

## 2. AgentState

真实定义见 [state.py](../../src/core/graph/state.py):

```python
class AgentState(TypedDict, total=False):
    # parse_request / API 层写入
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str                        # 有效用户 ID (effective_user_id), 非归属模式为空
    actor_id: str | None                    # 当前 Actor ID (v0.3.0)
    persona: str
    persona_name: str
    persona_id: str                         # 人格标识 (v0.3.0, 不再硬编码)
    thread_id: str
    proxy_thinking_enabled: bool
    space_id: str | None                    # 会话空间 ID (v0.3.0)
    channel_type: str | None                # "direct" | "group" (v0.3.0)

    # proxy_thinking 写入
    proxy_thinking_result: str | None

    # main_dialogue 写入
    response: str
    response_chunks: list[bytes]
    emotion_analysis: dict                  # 预计算的情绪分析 (v0.3.0, 供后续节点共享)

    # memory_analysis 写入
    new_memories: list[dict]
    decay_evaluations: list[dict]           # v0.3.0: 节点始终返回 [], 衰减由确定性公式处理
    decay_targets: list[dict]

    # relationship_analysis 写入
    relationship_delta: dict

    # 全局
    errors: list[str]
    stream_mode: bool
```

**不在 state 中的东西**:
- `retrieved_memories` / `permanent_memories` 由 `main_dialogue_node` 内部处理, 不上共享状态
- `main_model` / `source_frontend` / `api_key_id` / `external_event_id` 是请求级附加键, 由 [forward.py](../../src/api/routes/forward.py) 注入, 不在 TypedDict 定义中
- 上游 API Key 与请求体不进 state, API 层预处理完毕

### 2.1 状态流转

`actor_id` / `space_id` / `channel_type` / `persona_id` 由 API 层 ([forward.py](../../src/api/routes/forward.py)) 在进入图之前写入 state, 节点不自行解析身份。详见 [身份子系统](identity.md)。

```
parse_request       写: extracted_new, source_user
       │
       ▼
proxy_thinking      读: extracted_new, source_user
(可选)              写: proxy_thinking_result
       │
       ▼
main_dialogue       读: extracted_new, persona, proxy_thinking_result
                    写: response, response_chunks, emotion_analysis
       │
       ├───────────────────────────┐  (并行分支, 无先后)
       ▼                           ▼
relationship_analysis        memory_analysis
读: extracted_new,           读: extracted_new, emotion_analysis
    emotion_analysis         写: new_memories, decay_evaluations
写: relationship_delta              (decay_evaluations 始终 [])
       │                           │
       └───────────────┬───────────┘
                       ▼
                      END
```

---

## 3. 节点

真实节点见 [builder.py:54-58](../../src/core/graph/builder.py#L54-L58), 一共 **5 个**:

| 节点 | 类型 | 职责 | 是否阻塞响应 |
|------|------|------|-------------|
| `parse_request` | 预处理 | 消息提取 + user 标识 | 是 |
| `proxy_thinking` | Agent (CoT) | 可选; 为主对话生成 CoT 推理 | 是 (若启用) |
| `main_dialogue` | Agent | 拼上下文 + 预计算情绪 + 生成回复 | 是 |
| `memory_analysis` | Agent (ReAct) | 提取候选记忆 + 受众过滤查重 + 向量入库; 非归属模式跳过 | 否 (流式模式下后台跑) |
| `relationship_analysis` | Agent (ReAct) | 亲密度/信任度分析; 非归属模式跳过 | 否 (流式模式下后台跑) |

**没有独立的 `vector_index` 节点**——嵌入向量的写入 (Chroma) 在 `memory_analysis_node` 内由 `MemoryLifecycle.store_candidate()` 顺手完成。

### 3.1 节点实现模式

节点是 async 函数, 接收 state, 返回 state 增量:

```python
async def memory_analysis_node(state: AgentState) -> dict:
    settings = get_settings()
    source_user = state["source_user"]
    # 非归属模式: 无有效用户, 不写入任何私有记忆
    if not source_user:
        return {"new_memories": [], "decay_evaluations": []}

    forwarder, resolver = _make_multi_forwarder_with_resolver()
    memory_store = SqliteMemoryStore(...)
    try:
        vector_store = VectorStore(...)
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)

        # 受众上下文: 检索按当前会话受众过滤
        rel = await memory_store.get_relationship(
            state.get("persona_id", "default"), source_user)
        tools = [
            make_vector_search_tool(retriever, _retrieval_context(state, rel)),
        ]

        # 从 state 获取预计算的情绪分析 (由 main_dialogue 计算)
        emotion_analysis = state.get("emotion_analysis", {})

        out = await run_memory_analysis(
            forwarder=forwarder, source_user=source_user,
            conversation=..., tools=tools,
            persona_name=..., persona_addressing=...,
            user_addressing=..., relation_context=...,
            emotion_analysis=emotion_text,
            max_iterations=4,
        )
        # 写入 SQLite + Chroma (带 space_id)
        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder, resolver=resolver)
        for cand in out.new_memories:
            await lifecycle.store_candidate(
                cand, source_user=source_user, space_id=state.get("space_id"),
            )
        # 确定性衰减: 公式批量更新, 不再由 LLM 评估
        await lifecycle.run_deterministic_decay()
        return {"new_memories": [...], "decay_evaluations": []}
    finally:
        await forwarder.close()
```

**关键变化 (v0.3.0)**:
- `actor_id` / `space_id` / `channel_type` / `persona_id` 由 API 层在进入图之前写入 state
- `get_relationship` 使用 `state.get("persona_id", "default")`, 不再硬编码 `"default"`
- `_retrieval_context(state, rel)` 构建受众上下文, 传给 `make_vector_search_tool` 做受众过滤
- `store_candidate` 传入 `space_id` 标记记忆归属空间
- 情绪分析由 `main_dialogue_node` 预计算 (`_compute_emotion`), 通过 `emotion_analysis` 字段共享给记忆分析和关系分析节点
- 衰减由 `run_deterministic_decay()` 用确定性公式批量处理, 不再由 LLM 驱动

完整实现见 [nodes.py](../../src/core/graph/nodes.py)。

---

## 4. 图拓扑

### 4.1 拓扑图

```
parse_request
      │
      ├─ proxy_thinking_enabled? ──► proxy_thinking
      │                                   │
      └───────────────────────────────► main_dialogue
                                            │
                              ┌─────────────┴─────────────┐  (并行 fan-out)
                              ▼                           ▼
                    relationship_analysis          memory_analysis
                              │                           │
                              └─────────────┬─────────────┘
                                            ▼
                                           END
```

### 4.2 真实边定义

见 [builder.py](../../src/core/graph/builder.py):

```python
graph.add_node("parse_request", parse_request_node)
graph.add_node("proxy_thinking", proxy_thinking_node)
graph.add_node("main_dialogue", main_dialogue_node)
graph.add_node("relationship_analysis", relationship_analysis_node)
graph.add_node("memory_analysis", memory_analysis_node)

graph.set_entry_point("parse_request")

graph.add_conditional_edges("parse_request", _route_after_parse, {
    "proxy_thinking": "proxy_thinking",
    "main_dialogue": "main_dialogue",
})
graph.add_edge("proxy_thinking", "main_dialogue")

# 并行分支
graph.add_edge("main_dialogue", "relationship_analysis")
graph.add_edge("main_dialogue", "memory_analysis")

graph.add_edge("relationship_analysis", END)
graph.add_edge("memory_analysis", END)
```

### 4.3 流式模式下的同步 / 异步边界

流式请求下, `main_dialogue` 的 SSE chunks 边收边返给客户端 (在 [forward.py](../../src/api/routes/forward.py) 里直接由 `_handle_stream` 处理, 不完全走图); 主对话完成后, 记忆图通过 `asyncio.create_task(_run_memory_graph(...))` 在后台执行, 不阻塞响应。

```
主线 (阻塞客户端):
  parse_request → (proxy_thinking) → main_dialogue → SSE 流回客户端

后台任务:
  memory_analysis ∥ relationship_analysis → END
```

非流式模式则完整 `graph.ainvoke`, 所有节点串在同一次请求生命周期内。

---

## 5. ReAct 循环

**驱动器**: [src/core/agents/base.py `run_react_loop`](../../src/core/agents/base.py)。

流程:

```
1. 组装 messages: [system_prompt, user_prompt] + tools schema
2. Forwarder.chat(messages, tools=tools_schema, tool_choice="auto")
3. 模型响应:
     - tool_calls != []  → 执行工具 → 结果作为 role=tool 消息追加 → 回到 2
     - tool_calls == []  → content 就是最终输出, 结束
4. 达到 max_iterations 未终止 → 返回 error
```

工具的**调用顺序、次数、是否调用**均由模型 function_call 输出决定, 本地不硬编码——这是 Agent 与确定性管道的本质差别。

### 5.1 关键实现细节

- **`enable_thinking` 默认 False**: [base.py:123](../../src/core/agents/base.py#L123) 默认 `extra_body={"enable_thinking": False}`, 关闭 Qwen3 系模型的 thinking 输出, 保证结构化 JSON 干净。用主对话时可按需覆盖 `extra_body`。
- **JSON 解析**: [factory.py `_extract_json`](../../src/core/agents/factory.py) 提供两级容错——先按行提取 `{...}` 块, 失败则用正则给未加引号的键补引号后重试。
- **提示词渲染必须用 `str.replace`**: prompt 里含字面 JSON, 不能用 `str.format` (见 [dev-decisions.md](../dev-decisions.md))。

---

## 6. Checkpoint 的角色变化 (v0.2.6)

v0.2.5 及以前, LangGraph 的 `MemorySaver` checkpoint 被同时用作两件事: (a) 图内节点共享 state; (b) 跨请求短期记忆 (按 `thread_id` 关联多轮对话)。

v0.2.6 把 (b) 剥离到服务端 `conversation_turns` 表 (见 [memory-system.md §1.4](memory-system.md#14-短期记忆-v026--跨前端对话流水))。理由:

- **`thread_id` 由客户端决定**, 不同前端各起各的 thread, 无法跨前端同步 — 直接违背 Mnemosync"多前端 = 同一用户"的核心承诺
- **进程内 MemorySaver 重启即失** — 服务器视角的记忆真相不该依赖进程生命周期
- **主对话在流式路径下已不完整走图** — `_handle_stream` 直接从 forward.py 装填 messages + 转发, 图 (`_run_memory_graph`) 仅在后台跑记忆分析和关系分析, checkpoint 作跨请求上下文用不上

现在 checkpoint 仅剩单请求内节点间 state 共享的角色 ([graph] `checkpoint_backend` 配置仍保留), 生产也不再需要切 SqliteSaver。

短期 (conversation_turns) 与长期 (Chroma + SQLite) 分工:

| 维度 | 短期 (v0.2.6) | 长期 |
|------|-------------|------|
| 存储 | `data/conversation.db` `conversation_turns` 表 | Chroma + `data/memory.db` |
| 内容 | 逐字 user/assistant turn (append-only) | 结构化 MemoryEntry (抽取后的事实) |
| 生命周期 | 时间窗 (默认 7d) 后台清理 | 衰减模型 + 手动 Prune |
| 检索 | 按 ts 直取 + 双窗裁剪 | embedding + rerank |
| 装填时机 | forward.py `build_short_term_history` | forward.py `render_main_dialogue_system` + 工具调用 |

---

## 7. 错误处理

所有节点用 try/except 包住, 单节点失败不影响并行分支:

| 节点 | 失败影响 | 处理 |
|------|---------|------|
| parse_request | 阻塞 | 返回 500 |
| proxy_thinking | 退化 | 记 warning, 继续 main_dialogue |
| main_dialogue | 阻塞 | 返回 500 (记忆已加载但生成失败) |
| memory_analysis | 本次不入库 | 记 error, 关系分析继续 |
| relationship_analysis | 关系不更新 | 记 error, 记忆分析继续 |

所有节点都会把错误信息追加到 `state.errors`。

---

## 8. 模块结构

```
src/core/graph/
├── __init__.py
├── builder.py     # StateGraph 组装, 编译入口 build_graph()
├── nodes.py       # 5 个节点实现
└── state.py       # AgentState TypedDict
```

Agent 执行函数在 [src/core/agents/](../../src/core/agents/) (`factory.py` / `base.py` / `prompts/`)。

---

## 9. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [架构总览](../architecture.md) | 顶层视图 |
| [多 Agent 设计](agents.md) | 每个 Agent 节点的详细规格 |
| [身份子系统](identity.md) | `actor_id` / `space_id` / `channel_type` / `persona_id` 由 API 层写入 state, 节点不自行解析身份 |
| [Forwarder](forward.md) | 节点通过它调模型 |
| [LLM 服务管理](llm-service.md) | 节点读取的模型配置来源 |
| [记忆系统](memory-system.md) | checkpoint (短) + Chroma+SQLite (长) |
| [消息处理流程](message-processing.md) | 端到端的图执行轨迹 |

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 初始 StateGraph 编排、条件路由、并行节点 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 5 节点 (无 vector_index)、AgentState 字段修正、模块路径修正为 `src/core/graph/` |
| v0.2.6 | 2026-07-18 | §6 checkpoint 不再承担跨请求短期记忆, 迁到 `conversation_turns`; 保留 checkpoint 仅作单请求内 state 共享 |
| v0.3.0 | 2026-07-26 | 身份字段: AgentState 新增 `actor_id` / `persona_id` / `space_id` / `channel_type` / `emotion_analysis`; `source_user` 语义改为 `effective_user_id` (可为空, 非归属模式); 节点加非归属 guard; 情绪预计算 (`_compute_emotion`) 共享; 衰减由确定性公式 (`run_deterministic_decay`) 处理; 受众过滤 (`_retrieval_context` + `AudienceFilter`) 贯穿检索; 交叉链接 [身份子系统](identity.md) |
