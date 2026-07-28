# 辅助 Agent 统一运行契约 | RFC

> **状态**: RFC（征求意见稿）
> **日期**: 2026-07-28
> **作者**: HarryHelloo
> **关联**: [agents.md](../modules/agents.md), [forward.md](../modules/forward.md), [future-persona-architecture.md](future-persona-architecture.md)
> **关键词**: AgentSpec, AgentRun, 辅助 Agent, 超时, 后台生命周期, 内部工具隔离

---

## 1. 动机

### 1.1 现状

当前 Mnemosync 有 6 个辅助 Agent（不含主对话），它们的运行方式各不相同：

| Agent | 位置 | 调用方式 | 超时 | 输出 |
|---|---|---|---|---|
| 提示词清洗 | `factory.py` | `run_simple_completion()` | 无 | 清洗后文本 |
| Expressor | `factory.py` | `run_simple_completion()` | 无 | 改写后文本 |
| 代理推理 | `factory.py` | `run_simple_completion()` | 无 | 推理文本 |
| 记忆分析 | `factory.py` | `run_agent()` (ReAct) | `max_iterations=4` | `CandidateMemory[]` |
| 关系分析 | `factory.py` | `run_agent()` (ReAct) | `max_iterations=2` | `RelationshipDelta` |
| 情绪分析 | `extraction.py` | 确定性规则 | N/A | 情绪标签 |

### 1.2 问题

1. **没有统一的运行契约**: 每个 Agent 的调用方式、超时、取消、错误处理各有实现，难以统一治理。
2. **后台生命周期不明确**: `_run_memory_graph` 作为 `asyncio.create_task` 运行，父请求结束后仍可能运行。当前实现没有取消机制。
3. **没有可观测性契约**: Agent 调用链无法追踪到原始请求；无法区分"模型生成的文本"和"Agent 生成的文本"。
4. **内部工具隔离不严格**: 辅助 Agent 通过闭包工厂获得绑定好的工具，但无统一的工具声明和权限模型。
5. **没有结果持久化**: Agent 运行结果不落库，无法审计和复现。

### 1.3 目标

1. 为所有辅助 Agent 定义统一的 `AgentSpec` 和 `AgentRun` 契约。
2. 提供超时、取消、重试和错误报告的基础设施。
3. 建立内部工具声明、注入和隔离的规范。
4. 为后台 Agent 提供生命周期管理（父请求结束后自动取消）。
5. 保留 Agent 运行的可追溯性。

---

## 2. AgentSpec

```python
@dataclass
class AgentSpec:
    """Agent 规格定义."""

    name: str                         # 唯一标识, 如 "memory_analysis"
    purpose: str                      # 一句话描述
    model_role: ModelType             # MAIN / ASSIST / EMBEDDING / RERANK
    prompt_version: str               # 当前使用的主 prompt 版本
    runner_type: Literal["simple", "react"]  # 运行方式
    allowed_tools: list[str]          # 允许的内部工具名列表
    timeout_seconds: float            # 单次运行超时
    max_iterations: int               # ReAct 最大迭代 (simple=1)
    output_schema: type | None        # 期望输出类型 (用于类型校验)
    privacy_scope: str                # 隐私范围: user | space | global
```

### 2.1 初始 Agent 规格

```text
AgentSpec(name="prompt_cleaning",     purpose="清洗客户端 system 消息",
          model_role=ASSIST, runner_type="simple",
          allowed_tools=[], timeout=15, max_iterations=1)

AgentSpec(name="expressor",           purpose="改写为自然群聊表达",
          model_role=ASSIST, runner_type="simple",
          allowed_tools=[], timeout=10, max_iterations=1)

AgentSpec(name="proxy_thinking",      purpose="生成代理推理",
          model_role=ASSIST, runner_type="simple",
          allowed_tools=[], timeout=30, max_iterations=1)

AgentSpec(name="memory_analysis",     purpose="提取候选记忆",
          model_role=ASSIST, runner_type="react",
          allowed_tools=["vector_search"], timeout=60, max_iterations=4,
          privacy_scope="user")

AgentSpec(name="relationship_analysis", purpose="计算关系增量",
          model_role=ASSIST, runner_type="react",
          allowed_tools=["update_addressing"], timeout=30, max_iterations=2,
          privacy_scope="user")
```

---

## 3. AgentRun

```python
@dataclass
class AgentRun:
    """一次 Agent 运行记录."""

    run_id: str                       # 唯一标识
    parent_request_id: str | None     # 触发此运行的 HTTP 请求 ID
    agent_name: str                   # AgentSpec.name
    input_messages: list[dict] | None # 输入消息 (仅保留非隐私部分)
    input_event_ids: list[str]        # 触发事件的 event_id 列表
    base_version: str | None          # 输入时空间的 committed_sequence
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "ok", "failed", "timeout", "cancelled"]
    tool_trace: list[dict]            # 工具调用记录 [{tool, input, output, duration}]
    usage: dict                       # {prompt_tokens, completion_tokens, total_tokens}
    structured_result: Any | None     # 类型化输出 (如 CandidateMemory[])
    error: str | None                 # 错误信息
```

### 3.1 运行记录持久化

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    parent_request_id TEXT,
    agent_name TEXT NOT NULL,
    input_event_ids TEXT,        -- JSON array
    base_version TEXT,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'running',
    tool_trace TEXT,             -- JSON array
    usage TEXT,                  -- JSON object
    structured_result TEXT,      -- JSON
    error TEXT
);

CREATE INDEX idx_agent_runs_parent ON agent_runs(parent_request_id);
CREATE INDEX idx_agent_runs_agent ON agent_runs(agent_name);
CREATE INDEX idx_agent_runs_started ON agent_runs(started_at DESC);
```

---

## 4. 超时与取消

### 4.1 超时

每个 Agent 运行时应包在 `asyncio.wait_for()` 中:

```python
try:
    result = await asyncio.wait_for(
        _run_agent_inner(spec, input_data),
        timeout=spec.timeout_seconds,
    )
    status = "ok"
except asyncio.TimeoutError:
    status = "timeout"
    result = None
```

超时后应取消底层任务（asyncio 自动传播 CancelledError）。

### 4.2 取消

后台 Agent（`_run_memory_graph`）在父请求结束后应可被取消：

```python
# 父请求结束时触发
run.cancel()  # 设置 cancellation_token
```

方案一：通过 `asyncio.Task` 的 `cancel()` 传播。
方案二：通过共享的 `cancellation_token: asyncio.Event` 由 Agent 内轮询检查。

**推荐方案一**，因为 `asyncio.Task.cancel()` 传播到所有 `await` 点，对现有代码影响最小。不足是所有 Agent 当前没有处理 `CancelledError` 的逻辑，需要加 `try/finally` 确保资源释放。

### 4.3 后台记忆图的当前行为

```python
asyncio.create_task(_run_memory_graph(initial_state, collected_chunks, graph_config))
```

`_run_memory_graph` 当前以 fire-and-forget 方式运行。父请求结束后：
- 如果请求是 HTTP 长连接（stream），请求结束后 `asyncio.create_task` 创建的任务仍然运行。
- 如果请求是短连接（non-stream），同理。
- 只有在应用关闭时，所有后台任务被隐式取消。

**改进**: 将 `_run_memory_graph` 包装为 `AgentRun`，持有引用以便在请求结束时取消。

---

## 5. 内部工具隔离

### 5.1 当前实现

`make_vector_search_tool()` / `make_update_addressing_tool()` 通过闭包绑定依赖和受众上下文。

### 5.2 问题

1. 辅助 Agent 可以访问向量检索，但没有"只能搜自己桶"的硬性保证（靠受众过滤而非隔离）。
2. 辅助 Agent 不应获得客户端工具（poke, react 等）——当前由 `tools` 列表控制，但无显式检查。
3. 内部工具与客户端工具的命名空间未隔离。

### 5.3 规范

1. 每个 `AgentSpec` 显式声明 `allowed_tools`，运行时校验：未列出的工具不能被注入。
2. 内部工具统一加 `_` 前缀：`_vector_search`, `_update_addressing`。
3. 主模型不能调用内部工具（内部工具注入时过滤）；辅助 Agent 不能调用客户端工具。
4. 工具调用记录记入 `AgentRun.tool_trace`。

### 5.4 与 §8.3 (跨平台身份绑定) 的关系

§8.3 已实现内部 tool 注册表 `InternalToolRegistry`，其工具注入主模型并由出站拦截。这与辅助 Agent 的内部工具不同：

| | 主模型内部工具 | 辅助 Agent 内部工具 |
|---|---|---|
| 注入对象 | 主模型（`main_dialogue_node`） | 辅助 Agent（`memory_analysis_node`） |
| 拦截方式 | 出站过滤 + 服务端执行 | 闭包工厂（已有） |
| 返回客户端 | 否 | 否 |
| 注册表 | `InternalToolRegistry` | `AgentSpec.allowed_tools` |

长期看，两者应统一为同一个注册表，但本轮不合并。

---

## 6. 实现路径

### 阶段一: 基础设施

1. 新建 `src/core/agents/spec.py`，定义 `AgentSpec` / `AgentRun` / 注册表。
2. 新建 `src/persistence/agent_run_store.py`，`agent_runs` 表的 CRUD。
3. 将现有 6 个 Agent 转为 `AgentSpec` 定义。

### 阶段二: 运行时集成

1. 修改 `factory.py` 中的 `run_agent()` / `run_simple_completion()`，包装超时和运行记录。
2. 修改 `_run_memory_graph`，包装为 `AgentRun` 并支持取消。
3. 在 `debug_bus` 中发射 Agent 运行开始/结束事件。

### 阶段三: 工具隔离

1. 给内部工具加 `_` 前缀。
2. 实现 `allowed_tools` 运行时校验。
3. 统一 `InternalToolRegistry` 和 `AgentSpec.allowed_tools`。

### 阶段四: 面板可观测性

1. `GET /panel/admin/agent-runs` 列出最近的 Agent 运行记录。
2. `GET /panel/admin/agent-runs/{id}` 单条详情（含 tool_trace）。
3. 面板显示当前正在运行的 Agent。

---

## 7. 未解决的问题

1. **`_run_memory_graph` 的取消策略**: 父请求结束后立即取消，还是等待当前正在写入的记忆入库完成？推荐"等待当前一轮记忆分析完成，但不再启动新的"。
2. **运行记录隐私**: `input_messages` 可能包含私有信息。默认只保留非隐私部分（或仅在调试模式下保存）。
3. **Agent 之间的依赖**: 情绪分析在 main_dialogue_node 中预计算，然后传给 memory_analysis 和 relationship_analysis。这是"状态共享"而非 Agent 间调用。在 AgentSpec 框架中如何表示这种依赖？
4. **与 Expressor 的关系**: Expressor 改写发生在 main_dialogue_node 中，不是独立 Agent。但它有独立 prompt、独立模型、超时和降级逻辑，适合包装为 AgentRun。是否需要？
5. **多候选回退**: 当前 `run_main_dialogue` 有多候选回退逻辑。辅助 Agent 是否也需要多候选？当前辅助 Agent 用 `run_simple_completion` 走 ASSIST 角色的首个候选，无回退。

---

## 8. 不纳入范围

- 人格结构化定义（由 `future-persona-architecture.md` §3 负责）。
- 主对话 Agent 的架构（其生命周期与 HTTP 请求一致，无需额外管理）。
- 分布式部署下的 Agent 协调（单进程 `asyncio` 足够）。
