# LangGraph 编排模块 | LangGraph Orchestration

> **模块版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

LangGraph 是 Mnemosync 多 Agent 系统的**编排骨架**。它负责决定何时调用哪个 Agent、如何在 Agent 间传递状态、如何驱动 ReAct/CoT 循环、如何持久化短期记忆。

### 1.1 定位

> ⚠️ **重要**：LangGraph **不执行模型推理**。它只做编排——组装请求、解析响应、驱动循环、管理状态。所有模型推理发生在远端服务商，通过 [Forwarder](forward.md) 调用。

```
LangGraph 的职责（本地）：
  ✓ 定义节点（Agent）和边（流转关系）
  ✓ 管理 AgentState（共享状态）
  ✓ 驱动 ReAct/CoT 循环（解析 function_call，喂回结果）
  ✓ 条件路由（如代理思考是否启用）
  ✓ 并行节点编排（衰减 + 关系分析）
  ✓ Checkpoint（短期记忆）

LangGraph 不做的事（远端）：
  ✗ 模型推理（在服务商侧）
  ✗ Think/Act 的"Think"（在模型侧）
```

### 1.2 为什么用 LangGraph

| 需求 | LangGraph 提供 |
|------|----------------|
| 多 Agent 编排 | StateGraph 节点/边模型 |
| 条件流转 | conditional_edges |
| 并行节点 | 原生并行执行 |
| 短期记忆 | Checkpoint（MemorySaver / SqliteSaver） |
| ReAct 循环驱动 | ToolNode + 模型 function_call 协议 |
| 可观测性 | 节点执行追踪、状态快照 |

---

## 2. 状态定义 (AgentState)

所有 Agent 通过共享的 `AgentState` 通信。状态在节点间流转，每个节点读取所需字段、写入产出字段。

```python
from typing import TypedDict

class AgentState(TypedDict):
    # === 请求上下文（parse_request 写入） ===
    messages: list[dict]              # 原始 messages（OpenAI 格式）
    extracted_new: list[dict]         # 提取出的新内容
    source_user: str                  # 来源用户标识
    persona: str                      # 人格 system prompt
    thread_id: str                    # 会话 ID（checkpoint 用）
    proxy_thinking_enabled: bool      # 是否启用代理思考

    # === 代理思考（proxy_thinking 写入） ===
    proxy_thinking_result: str | None # CoT 推理结果

    # === 检索结果（vector_search 工具写入） ===
    retrieved_memories: list[dict]    # 语义检索的相关记忆
    permanent_memories: list[dict]    # 永久记忆（始终加载）

    # === 主对话输出（main_dialogue 写入） ===
    response: str                     # 生成的回复

    # === 记忆分析输出（memory_analysis 写入） ===
    new_memories: list[dict]          # 新提取的记忆候选
    decay_evaluations: list[dict]     # 衰减评估结果

    # === 关系分析输出（relationship_analysis 写入） ===
    relationship_delta: dict          # 亲密度/信任度变化

    # === 全局 ===
    errors: list[str]                 # 错误汇总
```

### 2.1 状态流转图

```
parse_request
  写入: messages, extracted_new, source_user, persona, thread_id, proxy_thinking_enabled
       │
       ▼
proxy_thinking (可选)
  读取: extracted_new, retrieved_memories
  写入: proxy_thinking_result
       │
       ▼
main_dialogue
  读取: extracted_new, persona, proxy_thinking_result, permanent_memories, retrieved_memories
  写入: response
       │
       ▼（异步）
memory_analysis
  读取: extracted_new, source_user
  写入: new_memories, decay_evaluations
       │
       ├──────────┐
       ▼          ▼
relationship  vector_index
读取: 对话     读取: new_memories, decay_evaluations, relationship_delta
写入: delta    写入: 持久化到 ChromaDB + SQLite
```

---

## 3. 节点定义 (Nodes)

### 3.1 节点清单

| 节点 | 类型 | 职责 | 是否阻塞响应 |
|------|------|------|--------------|
| `parse_request` | 基础设施 | 鉴权 + 消息提取 | 是 |
| `proxy_thinking` | Agent (CoT) | 可选；为弱模型代理思考 | 是（若启用） |
| `main_dialogue` | Agent | 主对话生成回复 | 是 |
| `memory_analysis` | Agent (ReAct) | 提取记忆 + 衰减评估 | 否（异步） |
| `relationship_analysis` | Agent (CoT) | 亲密度变化分析 | 否（异步并行） |
| `vector_index` | 工具节点 | 入库 + 索引更新 | 否（异步并行） |

### 3.2 节点实现模式

每个 Agent 节点是一个函数，接收 state、返回 state 的部分更新：

```python
async def memory_analysis_node(state: AgentState) -> dict:
    """记忆分析 Agent 节点（ReAct 驱动）。"""
    # 1. 组装 prompt + 工具定义
    prompt = build_memory_analysis_prompt(state["extracted_new"])
    tools = [vector_search, emotion_analyzer, time_decay_calculator]

    # 2. 通过 Forwarder 调用辅助模型，驱动 ReAct 循环
    #    循环内部：模型返回 function_call → 执行工具 → 喂回结果 → 再请求
    result = await run_react_loop(
        model=get_assist_model(),       # 从 LLM 服务配置读取
        prompt=prompt,
        tools=tools,
        forwarder=get_forwarder(),
        max_iterations=5,
    )

    # 3. 返回状态更新
    return {
        "new_memories": result["new_memories"],
        "decay_evaluations": result["decay_evaluations"],
        "errors": result.get("errors", []),
    }
```

> `run_react_loop` 是 LangGraph 提供的 ReAct 循环驱动器，内部通过 Forwarder 反复调用模型直到输出最终结果。

---

## 4. 图拓扑 (Graph Topology)

### 4.1 流转图

```
                          ┌─────────────┐
                          │   START     │
                          └──────┬──────┘
                                 │
                          ┌──────▼──────┐
                          │parse_request│
                          └──────┬──────┘
                                 │
                    ┌────────────┴────────────┐
                    │ should_proxy_think?     │
                    └────────────┬────────────┘
                    Yes │              │ No
                        ▼              │
                  ┌──────────┐         │
                  │proxy_    │         │
                  │thinking  │         │
                  └────┬─────┘         │
                       └───────┬───────┘
                               ▼
                         ┌─────────────┐
                         │main_dialogue│ ──→ 流式返回给前端
                         └──────┬──────┘
                                │
                                ▼（异步，响应已返回）
                      ┌─────────────────┐
                      │memory_analysis  │
                      └────┬────────┬───┘
                           │        │
            ┌──────────────┘        └──────────────┐
            ▼                                      ▼
    ┌──────────────────┐                  ┌──────────────────┐
    │relationship_     │                  │vector_index      │
    │analysis          │                  │(入库 + 索引)      │
    └────────┬─────────┘                  └────────┬─────────┘
             │                                     │
             └────────────────┬────────────────────┘
                              ▼
                          ┌───────┐
                          │  END  │
                          └───────┘
```

### 4.2 边定义

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

# 节点
graph.add_node("parse_request", parse_request_node)
graph.add_node("proxy_thinking", proxy_thinking_node)
graph.add_node("main_dialogue", main_dialogue_node)
graph.add_node("memory_analysis", memory_analysis_node)
graph.add_node("relationship_analysis", relationship_analysis_node)
graph.add_node("vector_index", vector_index_node)

# 入口
graph.set_entry_point("parse_request")

# 条件路由：是否启用代理思考
graph.add_conditional_edges(
    "parse_request",
    should_proxy_think,
    {
        True: "proxy_thinking",
        False: "main_dialogue",
    },
)
graph.add_edge("proxy_thinking", "main_dialogue")

# 主对话后进入异步记忆分析
graph.add_edge("main_dialogue", "memory_analysis")

# 记忆分析后并行分支（LangGraph 原生支持 fan-out）
graph.add_edge("memory_analysis", "relationship_analysis")
graph.add_edge("memory_analysis", "vector_index")

# 汇聚到 END
graph.add_edge("relationship_analysis", END)
graph.add_edge("vector_index", END)

# 条件路由函数
def should_proxy_think(state: AgentState) -> bool:
    return state.get("proxy_thinking_enabled", False)
```

### 4.3 同步与异步的边界

```
┌──────────────────────────────────────────┐
│ 同步路径（阻塞响应）                       │
│  parse_request → (proxy_thinking) →       │
│  main_dialogue → 流式返回                  │
│                                              │
│  延迟约束: TTFT < 1s                        │
└──────────────────────────────────────────┘
                  │ asyncio.create_task
                  ▼
┌──────────────────────────────────────────┐
│ 异步路径（不阻塞响应）                      │
│  memory_analysis → (relationship_analysis │
│                     ‖ vector_index) → END  │
│                                              │
│  延迟约束: 1-5s，用户无感                   │
└──────────────────────────────────────────┘
```

> **实现要点**：`main_dialogue` 完成后立即返回响应给前端，后续节点通过 `asyncio.create_task` 在后台执行。这通过 LangGraph 的 `astream` 或自定义中断点实现。

---

## 5. ReAct 循环驱动

记忆分析 Agent 使用 ReAct，LangGraph 通过 `ToolNode` + 模型 `function_call` 协议驱动循环：

```
┌──────────────────────────────────────────────┐
│ memory_analysis 节点内部                       │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │ 1. 组装 prompt + tool schemas         │   │
│  │    → Forwarder 调用辅助模型            │   │
│  └──────────────┬───────────────────────┘   │
│                 │                            │
│                 ▼                            │
│  ┌──────────────────────────────────────┐   │
│  │ 2. 模型返回：function_call 或 final?  │   │
│  └──────────────┬───────────────────────┘   │
│                 │                            │
│        ┌────────┴────────┐                  │
│        ▼                 ▼                  │
│   function_call       final JSON            │
│        │                 │                  │
│        ▼                 ▼                  │
│  ┌──────────┐    写入 state, 结束           │
│  │ToolNode  │                                │
│  │执行工具  │                                │
│  └────┬─────┘                                │
│       │                                      │
│       ▼                                      │
│  工具结果喂回模型 ← 回到步骤 1                │
└──────────────────────────────────────────────┘
```

### 5.1 工具调用由模型自主决策

> **关键**：调用哪个工具、调用几次，由远端模型通过 function_call 协议决定。本地不写死 "先调 A 再调 B"。这是 Agent 区别于确定性管道的本质。

LangGraph 的 `ToolNode` 负责执行工具，但**何时调用工具**由模型输出决定：

```python
from langgraph.prebuilt import ToolNode

tools = [vector_search, emotion_analyzer, time_decay_calculator]
tool_node = ToolNode(tools)

# 模型可能输出的 function_call 示例：
# {"name": "vector_search", "args": {"query": "花生 过敏"}}
# → ToolNode 执行 vector_search → 结果喂回模型
# 模型继续推理，可能再输出：
# {"name": "emotion_analyzer", "args": {"text": "我对花生过敏"}}
# → ToolNode 执行 emotion_analyzer → 结果喂回模型
# 最终模型输出 JSON（最终结果），循环结束
```

---

## 6. 短期记忆（Checkpoint）

LangGraph 的 checkpoint 机制提供短期记忆——同一 `thread_id` 的多次请求共享状态历史。

### 6.1 配置

```python
from langgraph.checkpoint.memory import MemorySaver
# 或
from langgraph.checkpoint.sqlite import SqliteSaver

# 内存后端（默认，重启丢失）
checkpointer = MemorySaver()

# SQLite 后端（持久化，推荐生产）
checkpointer = SqliteSaver.from_conn_string("data/checkpoint.db")

graph_compiled = graph.compile(checkpointer=checkpointer)
```

### 6.2 工作方式

```
请求 1（thread_id="user_motor_session_1"）:
  state.messages = [user: "我叫马达"]
  → main_dialogue 生成回复 → checkpoint 保存

请求 2（thread_id="user_motor_session_1"，同 thread_id）:
  state.messages = [user: "我叫马达", assistant: "你好马达", user: "我压力大"]
  → main_dialogue 加载 checkpoint → 知道上文是"马达自我介绍"
  → 生成回复："马达，听说你压力大？"
```

> `thread_id` 由 API Gateway 从 source_user + 会话标识生成，确保同一用户同一会话的上下文连贯。

### 6.3 短期 vs 长期记忆

| 维度 | 短期记忆（Checkpoint） | 长期记忆（ChromaDB + SQLite） |
|------|----------------------|------------------------------|
| **存储** | LangGraph checkpoint | 向量库 + 元数据库 |
| **内容** | 当前会话对话历史 | 提取后的结构化记忆 |
| **生命周期** | 会话期间（或配置过期） | 持久化，按衰减模型管理 |
| **检索方式** | 按 thread_id 直接加载 | 语义检索（embedding + rerank） |
| **容量** | 受 token 窗口限制 | 受永久记忆限额 + 衰减管理 |

---

## 7. 错误处理与隔离

### 7.1 节点级隔离

```python
async def safe_node(node_fn, state):
    try:
        return await node_fn(state)
    except Exception as e:
        return {"errors": [f"{node_fn.__name__}: {e}"]}
```

### 7.2 失败传播规则

| 失败节点 | 影响 | 处理 |
|----------|------|------|
| parse_request | 阻塞 | 返回 400 错误 |
| proxy_thinking | 退化为正常模式 | 跳过，直接 main_dialogue |
| main_dialogue | 阻塞 | 返回 502 错误 |
| memory_analysis | 本次不存储 | 记录错误，继续 relationship_analysis |
| relationship_analysis | 本次不更新关系 | 记录错误，不影响 vector_index |
| vector_index | 记忆不入库 | 重试 3 次，失败则记日志 |

> **原则**：异步路径上的失败不阻塞响应。所有错误汇总到 `state.errors`，由后续日志处理。

---

## 8. 可观测性

### 8.1 状态快照

LangGraph 在每个节点执行后保存状态快照，可用于调试：

```python
# 获取某次执行的完整状态轨迹
trajectory = await graph_compiled.atranscript(thread_id, run_id)
# 每个节点的输入 state、输出 state、耗时、工具调用记录
```

### 8.2 ReAct 循环可见性

记忆分析 Agent 的 ReAct 循环过程可观测，便于调试和演示：

```
[DEBUG] memory_analysis round 1:
  Think: "用户说'我对花生过敏'，需确认是否冲突"
  Act: vector_search("花生 过敏")
  Observe: ["我喜欢吃花生酱"(0.82), "对海鲜过敏"(0.71)]

[DEBUG] memory_analysis round 2:
  Think: "过敏比偏好重要，需确认情绪"
  Act: emotion_analyzer("我对花生过敏")
  Observe: {emotion: neutral, intensity: 0.3}

[DEBUG] memory_analysis round 3 (final):
  Output: {memory_type: PERMANENT, importance: 1.0, overrides: "mem_abc"}
```

> 演示时打印这些日志，能让评分老师直观看到 ReAct 的 Think-Act-Observe 过程。

---

## 9. 模块结构

```
src/
├── graph/
│   ├── __init__.py
│   ├── state.py              # AgentState 定义
│   ├── nodes/
│   │   ├── parse_request.py
│   │   ├── proxy_thinking.py
│   │   ├── main_dialogue.py
│   │   ├── memory_analysis.py
│   │   ├── relationship_analysis.py
│   │   └── vector_index.py
│   ├── tools.py              # LangChain Tool 封装
│   └── builder.py            # StateGraph 组装
└── ...
```

---

## 10. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [架构设计](../architecture.md) | 本模块是架构的核心实现 |
| [Agent 设计](agents.md) | 每个 Agent 对应图中的一个节点 |
| [Forwarder](forward.md) | 节点通过 Forwarder 调用模型 |
| [LLM 服务管理](llm-service.md) | 节点从本模块读取模型配置 |
| [记忆系统](memory-system.md) | checkpoint 是短期记忆，ChromaDB 是长期记忆 |
| [消息处理流程](message-processing.md) | 流程即图的执行轨迹 |

---

## 11. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.2.0 | 2026-07-12 | 初始版本：StateGraph 编排、条件路由、并行节点、ReAct 驱动、Checkpoint |

---

> **维护者提示**:
> - 图拓扑改动需验证无循环依赖（除 ReAct 内部循环）。
> - 同步路径节点（parse_request / proxy_thinking / main_dialogue）的延迟直接影响 TTFT。
> - ReAct 循环必须有 max_iterations 上限，防止模型无限调用工具。
> - 切换 checkpoint 后端（memory → sqlite）不影响业务逻辑。