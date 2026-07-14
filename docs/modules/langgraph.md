# LangGraph 编排模块 | LangGraph Orchestration

> **模块版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-12
> **最后更新**: 2026-07-15
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
    # parse_request 写入
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str
    persona: str
    persona_name: str
    thread_id: str
    proxy_thinking_enabled: bool

    # proxy_thinking 写入
    proxy_thinking_result: str | None

    # main_dialogue 写入
    response: str
    response_chunks: list[bytes]

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

**不在 state 中的东西**:
- `retrieved_memories` / `permanent_memories` 由 `main_dialogue_node` 内部处理, 不上共享状态
- 上游 API Key 与请求体不进 state, API 层预处理完毕

### 2.1 状态流转

```
parse_request       写: extracted_new, source_user
       │
       ▼
proxy_thinking      读: extracted_new, source_user
(可选)              写: proxy_thinking_result
       │
       ▼
main_dialogue       读: extracted_new, persona, proxy_thinking_result
                    写: response, response_chunks
       │
       ├───────────────────────────┐  (并行分支, 无先后)
       ▼                           ▼
relationship_analysis        memory_analysis
读: extracted_new            读: extracted_new, decay_targets
写: relationship_delta       写: new_memories, decay_evaluations
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
| `main_dialogue` | Agent | 拼上下文 + 生成回复 | 是 |
| `memory_analysis` | Agent (ReAct) | 提取候选记忆 + 衰减评估 + 向量入库 | 否 (流式模式下后台跑) |
| `relationship_analysis` | Agent (ReAct) | 亲密度/信任度分析 | 否 (流式模式下后台跑) |

**没有独立的 `vector_index` 节点**——嵌入向量的写入 (Chroma) 在 `memory_analysis_node` 内由 `MemoryLifecycle.store_candidate()` 顺手完成。

### 3.1 节点实现模式

节点是 async 函数, 接收 state, 返回 state 增量:

```python
async def memory_analysis_node(state: AgentState) -> dict:
    settings = get_settings()
    forwarder = _make_forwarder()
    try:
        tools = [
            make_vector_search_tool(retriever),
            make_emotion_analyzer_tool(forwarder),
            make_time_decay_calculator_tool(memory_store),
        ]
        out = await run_memory_analysis(
            forwarder=forwarder,
            source_user=state["source_user"],
            conversation=..., tools=tools,
            decay_targets=decay_targets, max_iterations=6,
        )
        # 写入 SQLite + Chroma
        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder)
        for cand in out.new_memories:
            await lifecycle.store_candidate(cand, source_user=state["source_user"])
        return {"new_memories": [...], "decay_evaluations": [...]}
    finally:
        await forwarder.close()
```

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

## 6. 短期记忆 (Checkpoint)

LangGraph 的 checkpoint 提供会话级短期记忆。当前实现走 `MemorySaver` (进程内, 重启丢失); 生产可切 `SqliteSaver`。同一 `thread_id` 下的多次请求共享 state 历史, 主对话节点因此能感知上文。

短期 (Checkpoint) 与长期 (Chroma + SQLite) 的分工:

| 维度 | 短期 | 长期 |
|------|------|------|
| 存储 | LangGraph checkpoint | Chroma + SQLite |
| 内容 | 会话内 messages / state | 结构化 MemoryEntry |
| 生命周期 | 会话期 | 持久化 + 衰减 |
| 检索 | 按 thread_id 直取 | embedding + rerank |

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
