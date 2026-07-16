# 工具设计 | Tools

> **模块版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-11
> **最后更新**: 2026-07-16
> **作者**: HarryHelloo

---

## 1. 定位

Mnemosync 的 Agent (ReAct 循环节点) 通过 LangChain function_call 调用工具。工具本身是无状态的**闭包工厂** `make_*_tool(...)` 产出——工厂在图节点内组装依赖 (Forwarder / VectorStore / MemoryStore) 后返回一个绑定好依赖的 `@tool` 装饰函数, 交给对应 Agent。

**代码位置**: [src/tools/](../../src/tools/) (`vector_search.py` / `emotion_analyzer.py` / `time_decay_calculator.py` / `sentence_classifier.py`), 组装点在 [src/core/graph/nodes.py:208](../../src/core/graph/nodes.py#L208) (`memory_analysis_node` / `relationship_analysis_node`) 与 [src/api/routes/forward.py](../../src/api/routes/forward.py) (`prompt_cleaning` 在 API 层组装, 不进图)。

工厂全景:

| 工厂 | 内部函数名 | 直接调用者节点 |
|------|-----------|---------------|
| `make_vector_search_tool(retriever)` | `vector_search` | `memory_analysis_node` |
| `make_emotion_analyzer_tool(forwarder)` | `emotion_analyzer` | `memory_analysis_node`, `relationship_analysis_node` |
| `make_time_decay_calculator_tool(memory_store)` | `time_decay_calculator` | `memory_analysis_node` |
| `make_sentence_classifier_tool(forwarder)` | `classify_sentence_type` | API 层的 `run_prompt_cleaning` (提示词清洗 Agent) |

> 主对话不走 ReAct, 不绑定工具; 主对话所需的记忆检索由**节点外的 MemoryRetriever** 完成 (见 [message-processing.md](message-processing.md))。

---

## 2. `make_vector_search_tool(retriever)`

**签名**:

```python
def make_vector_search_tool(retriever: MemoryRetriever):
    @tool
    async def vector_search(
        query: str,
        top_k: int = 5,
        source_user: str | None = None,
    ) -> list[dict]: ...
    return vector_search
```

**依赖**: `MemoryRetriever(forwarder, vector_store, memory_store)`——[vector_search.py:49](../../src/tools/vector_search.py#L49)。同一个 `MemoryRetriever` 类既被工具使用, 也被主对话前置检索使用, 保证行为一致。

**执行流程** (见 `MemoryRetriever.search`):

1. `Forwarder.embed(query, model, dimensions)` → 查询向量
2. `VectorStore.search(vector, top_k=max(top_k*2, 10), source_user=...)` → 粗筛候选
3. 若 `settings.rerank` 存在: `Forwarder.rerank(query, documents, model, top_n=top_k)` 精排; 失败降级为纯 cosine top_k
4. 逐条 `SqliteMemoryStore.get_by_id(...)` 补完整字段

**返回**: `list[dict]`, 每条字段 (见 `RetrievedMemory.to_dict`):

```json
{
  "memory_id": "...",
  "content": "...",
  "similarity": 0.87,
  "relevance_score": 0.94,   // 无 rerank 时为 null
  "importance": 0.72,
  "memory_type": "normal",
  "emotional_tags": ["happy"],
  "source_user": "harry"
}
```

**注意**:
- `dimensions` 传给 embedding 端点 (若配置了)
- `source_user` 过滤在 VectorStore 层完成, 避免召回后再过滤造成 top_k 不足

---

## 3. `make_emotion_analyzer_tool(forwarder)`

**签名**:

```python
def make_emotion_analyzer_tool(forwarder: Forwarder):
    @tool
    async def emotion_analyzer(text: str) -> dict: ...
    return emotion_analyzer
```

**依赖**: 只需 `Forwarder` (走辅助模型)。

**执行流程** ([emotion_analyzer.py:51](../../src/tools/emotion_analyzer.py#L51) `analyze_emotion`):

1. `settings.chat.assist_model` + system prompt + user prompt (`EMOTION_PROMPT` 填入待分析文本)
2. `Forwarder.chat(temperature=0.1, response_format={"type": "json_object"}, extra_body={"enable_thinking": False})`——低温 + 强制 JSON + 关闭 Qwen3 thinking
3. 防御性剥离残留 `<think>...</think>`
4. `json.loads(content)` → `EmotionResult`

**返回**:

```json
{
  "emotion": "happy",       // happy|sad|angry|anxious|neutral|excited|grateful|stressed
  "intensity": 0.6,          // 0.0-1.0
  "category": "personal_sharing",
  "keywords": ["生日", "开心"],
  "summary": "用户对生日表达喜悦"
}
```

**注意**: `extra_body={"enable_thinking": False}` 是 DashScope Qwen3 系列的特有开关, 用来阻止思考流打乱 JSON 输出——参见 [dev-decisions.md](../dev-decisions.md)。

---

## 4. `make_time_decay_calculator_tool(memory_store)`

**签名**:

```python
def make_time_decay_calculator_tool(memory_store: SqliteMemoryStore):
    @tool
    async def time_decay_calculator(memory_id: str) -> dict: ...
    return time_decay_calculator
```

**依赖**: 只需 `SqliteMemoryStore`——**无外部 API 调用**。

**执行流程** ([time_decay_calculator.py:41](../../src/tools/time_decay_calculator.py#L41) `calculate_decay`):

1. `memory_store.get_by_id(memory_id)`; 缺则返回 `{"error": "memory not found"}`
2. `decay_rate_to_half_life(decay_rate)` → 半衰期天数 (映射表见 [src/core/memory/](../../src/core/memory/))
3. `time_factor = 0.5 ** (days_elapsed / half_life)`; 永久或 `decay_rate=0` 时 `time_factor=1.0`
4. `expiration_penalty = 0.01 if entry.is_expired else 1.0`
5. `access_bonus = log(access_count + 1) * ACCESS_BONUS_FACTOR`
6. `theoretical_priority = clamp(importance * time_factor * expiration_penalty + access_bonus, 0.0, 1.0)`
7. `days_to_forgotten`: 解 `importance * 0.5^(x/half_life) < 0.05` 反推
8. `current_state`: 永久 → `ACTIVE`; 否则由 `DecayState.from_priority(...)` 映射

**返回**:

```json
{
  "memory_id": "...",
  "days_elapsed": 12,
  "half_life_days": 33,
  "time_factor": 0.7794,
  "expiration_penalty": 1.0,
  "access_bonus": 0.0347,
  "theoretical_priority": 0.5657,
  "days_to_forgotten": 78,
  "current_state": "ACTIVE"
}
```

Agent 应在此**公式基线**之上进行 CoT 判断——例如考虑访问频率、情绪强度、与新记忆的关联——而不是直接把 `theoretical_priority` 当最终值。

---

## 5. `make_sentence_classifier_tool(forwarder)`

**签名**:

```python
def make_sentence_classifier_tool(forwarder: Forwarder):
    @tool
    async def classify_sentence_type(text: str) -> dict: ...
    return classify_sentence_type
```

**依赖**: 只需 `Forwarder` (走辅助模型)。

**用途**: 提示词清洗 Agent (见 [agents.md §6](agents.md#6-提示词清洗-agent)) 逐句判断客户端 system 消息中的每一句属于**人格描述** (`persona`) 还是**功能性指令** (`instruction`), 前者丢弃, 后者与服务器人格合并。

**执行流程** ([sentence_classifier.py](../../src/tools/sentence_classifier.py)):

1. 加载 `sentence_classifier` 提示词模板 (走 [PromptStore](../../src/core/prompts/store.py), 支持用户覆盖, 见 [agents.md §7](agents.md#7-自定义-agent-提示词)), 用 `str.replace("__TEXT__", text)` 填占位符
2. `settings.chat.assist_model` + 单次 `Forwarder.chat(response_format={"type": "json_object"}, extra_body={"enable_thinking": False})`, 不循环
3. `json.loads(content)` → `{type, confidence, reasoning}`

**返回**:

```json
{
  "type": "persona",             // persona | instruction | ambiguous
  "confidence": 0.87,             // 0.0-1.0
  "reasoning": "该句描述角色性格与说话风格, 属于人格设定"
}
```

**注意**:
- 与 emotion_analyzer 同样通过 `extra_body={"enable_thinking": False}` 关闭 Qwen3 thinking 保证 JSON 稳定
- 提示词模板占位符统一约定为 `__TEXT__` + `.replace` (不用 `.format`), 全项目一致, 详见 [dev-decisions.md 决策 6](../dev-decisions.md)
- 该工具唯一使用者是提示词清洗 Agent, 不在图内组装, 组装点在 [forward.py `create_chat_completion`](../../src/api/routes/forward.py)

---

## 6. 节点内组装

真实组装点 [nodes.py:207](../../src/core/graph/nodes.py#L207):

```python
# memory_analysis_node
retriever = MemoryRetriever(forwarder, vector_store, memory_store)
tools = [
    make_vector_search_tool(retriever),
    make_emotion_analyzer_tool(forwarder),
    make_time_decay_calculator_tool(memory_store),
]
out = await run_memory_analysis(
    forwarder=forwarder, source_user=source_user,
    conversation=conversation, tools=tools,
    decay_targets=decay_targets, max_iterations=6,
)
```

```python
# relationship_analysis_node
out = await run_relationship_analysis(
    forwarder=forwarder,
    current_relationship=current_rel_str,
    conversation=conversation,
    tools=[make_emotion_analyzer_tool(forwarder)],
    max_iterations=3,
)
```

**依赖注入方向**: 节点持有 forwarder / stores → 构造工厂闭包 → 交给 ReAct 循环 (`src/core/agents/react_runner.py`) → 循环调 `bind_tools` + `tool_call` 拿结果。

---

## 7. 错误行为

| 工具 | 触发条件 | 处理 |
|------|---------|------|
| `vector_search` | rerank 端点 4xx/5xx | 内部 `try/except` 降级为纯 cosine top_k, 不上抛 |
| `vector_search` | embedding 端点异常 | 直接抛 `UpstreamError`, ReAct 循环感知并终止 |
| `emotion_analyzer` | JSON 解析失败 | 抛 `json.JSONDecodeError` (未处理), 需 Agent prompt 保证 JSON 输出 |
| `time_decay_calculator` | memory_id 不存在 | 返回 `{"error": "memory not found", "memory_id": ...}`, 不抛 |
| `classify_sentence_type` | JSON 解析失败 | 抛 `json.JSONDecodeError`; 由提示词清洗 Agent 的保守降级兜底 (全部丢弃客户端 system 消息, 见 [agents.md §6.6](agents.md#66-失败降级-保守策略)) |

---

## 8. 与其他模块

| 模块 | 关系 |
|------|------|
| [LangGraph 编排](langgraph.md) | 3 个工具工厂在 `memory_analysis_node` / `relationship_analysis_node` 内组装; `sentence_classifier` 在 API 层组装, 不进图 |
| [Forwarder](forward.md) | 所有远端调用 (embed / rerank / chat) 的唯一出口 |
| [消息处理](message-processing.md) | 主对话前置检索复用 `MemoryRetriever` (非工具形式) |
| [提示词覆盖](agents.md#7-自定义-agent-提示词) | `sentence_classifier` 的提示词经 PromptStore 支持用户覆盖 |
| [配置](../configuration.md) | `[embedding]` / `[rerank]` / `[chat].assist_model` |

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-11 | 初始设计: 3 个工具 (向量检索 / 情绪 / 衰减) |
| v0.2.1 | 2026-07-15 | 与代码对齐: 全部改为 `make_*_tool()` 闭包工厂描述; 补 rerank 降级、`enable_thinking=False`、`current_state` 由 `DecayState.from_priority` 决定; 明确主对话不绑定工具 |
| v0.2.1 | 2026-07-16 | 新增第 4 个工具 `classify_sentence_type` (提示词清洗 Agent 专用); 提示词模板迁到 PromptStore, 占位符统一 `__TEXT__` + `.replace` |
