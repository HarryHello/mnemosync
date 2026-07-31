# 工具设计 | Tools

> **模块版本**: v0.3.0
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-11
> **最后更新**: 2026-07-26
> **作者**: HarryHelloo

---

## 1. 定位

Mnemosync 的 Agent (ReAct 循环节点) 通过 LangChain function_call 调用工具。工具本身是无状态的**闭包工厂** `make_*_tool(...)` 产出——工厂在图节点内组装依赖 (Forwarder / VectorStore / MemoryStore / 受众上下文) 后返回一个绑定好依赖的 `@tool` 装饰函数, 交给对应 Agent。

**代码位置**: [src/tools/](../../src/tools/) (`vector_search.py` / `update_addressing.py` / `emotion_analyzer.py`), 组装点在 [src/core/graph/nodes.py](../../src/core/graph/nodes.py) (`memory_analysis_node` / `relationship_analysis_node`)。

从 v0.2.3 起, 工具工厂接收的 forwarder 参数类型是 [MultiForwarder](forward.md), 按角色 (`assist` / `embedding` / `rerank`) 从 `role_bindings` 拉候选并 fallback。

工厂全景 (v0.3.0):

| 工厂 | 内部函数名 | 直接调用者节点 |
|------|-----------|---------------|
| `make_vector_search_tool(retriever, retrieval_ctx=None)` | `vector_search` | `memory_analysis_node` |
| `make_update_addressing_tool(memory_store, persona_id, user_id, actor_id=None)` | `update_addressing` | `relationship_analysis_node` (v0.2.10) |

> 主对话不走 ReAct, 不绑定工具; 主对话所需的记忆检索由**节点外的 MemoryRetriever** 完成 (见 [message-processing.md](message-processing.md))。

**已退役工具**:
- `emotion_analyzer` (ReAct 工具形态): v0.3.0 起不再是工具 — `main_dialogue_node` 通过 `analyze_emotion()` **预计算一次**, 结果经 `emotion_analysis` 文本参数传给记忆/关系两个 Agent, 避免重复 LLM 调用。`make_emotion_analyzer_tool` 工厂仍保留在 `emotion_analyzer.py`, 但无节点绑定。
- `time_decay_calculator`: v0.2.12 移除 — 衰减改为确定性公式 (`MemoryLifecycle.run_deterministic_decay()`), 不再由 LLM 驱动。
- `sentence_classifier`: v0.2.12 移除 — 提示词清洗改为单次 LLM 重写 (`run_prompt_cleaning`), 不再逐句分类。

---

## 2. `make_vector_search_tool(retriever, retrieval_ctx=None)`

**签名**:

```python
def make_vector_search_tool(
    retriever: MemoryRetriever,
    retrieval_ctx: RetrievalContext | None = None,   # v0.3.0
):
    @tool
    async def vector_search(
        query: str,
        top_k: int = 5,
        source_user: str | None = None,
    ) -> list[dict]: ...
    return vector_search
```

**依赖**: `MemoryRetriever(forwarder, vector_store, memory_store)`——[vector_search.py](../../src/tools/vector_search.py)。同一个 `MemoryRetriever` 类既被工具使用, 也被主对话前置检索使用, 保证行为一致。

**受众上下文 (v0.3.0)**: `memory_analysis_node` 组装工具时传入 `_retrieval_context(state, rel_for_addressing)` (由 `source_user` / `actor_id` / `space_id` / `channel_type` / 当前关系构建)。传入后 Agent 的查重检索只能看到当前受众可见的记忆——其他参与者的私有记忆不会进入 Agent 视野。不传则退回 v0.2.x 的 `source_user` 精确过滤路径。

**执行流程** (见 `MemoryRetriever.search`):

1. `MultiForwarder.embed(query)` → 查询向量 (v0.2.4 起走单绑定 embedding, 不 fallback)
2. 粗筛:
   - 有 `retrieval_ctx`: `VectorStore.search(vector, top_k=max(top_k*3, 15), where=AudienceFilter.build_chromadb_where(ctx))` — `$or` 超集 (自己桶 / PUBLIC / 本空间), 多取候选补偿精筛淘汰
   - 无 `retrieval_ctx`: `VectorStore.search(vector, top_k=max(top_k*2, 10), source_user=...)`
3. 精筛 (有 `retrieval_ctx` 时): 逐条 `AudienceFilter.is_visible(entry, ctx)` — 关系门槛与 deny/allow 策略只在 Python 层可判
4. 若 rerank 角色有绑定: `MultiForwarder.rerank(query, documents, top_n=...)` 精排; 失败降级为纯 cosine
5. 逐条 `SqliteMemoryStore.get_by_id(...)` 补完整字段, 凑满 `top_k` 即停

受众规则全表见 [memory-system.md §4.4](memory-system.md#44-受众过滤-v030)。

**返回**: `list[dict]`, 每条字段 (见 `RetrievedMemory.to_dict`):

```json
{
  "memory_id": "...",
  "content": "...",
  "similarity": 0.87,
  "relevance_score": 0.94,
  "importance": 0.72,
  "memory_type": "normal",
  "emotional_tags": ["happy"],
  "source_user": "group_60b7f32a..."
}
```

`source_user` 为 effective_user_id (v0.3.0): 绑组用户是组 ID, 未绑组是 actor ID。

---

## 3. 情绪分析 (`analyze_emotion`) — 预计算共享, 非工具

**代码**: [src/tools/emotion_analyzer.py](../../src/tools/emotion_analyzer.py)

v0.3.0 起情绪分析不再是 ReAct 工具。`main_dialogue_node` 在处理本轮消息时调用 `analyze_emotion(forwarder, text)` **一次** (经 `_compute_emotion` 包装), 结果写入 `state["emotion_analysis"]`, 记忆分析与关系分析两个 Agent 从提示词参数读取同一份结果——消除重复调用。

**执行流程** (`analyze_emotion`):

1. 走 assist 角色首位候选 + `EMOTION_PROMPT` 填入待分析文本
2. `MultiForwarder.chat(role=ASSIST, temperature=0.1, response_format={"type": "json_object"}, extra_body={"enable_thinking": False})`——低温 + 强制 JSON + 关闭 Qwen3 thinking
3. 防御性剥离残留 `...`
4. `json.loads(content)` → `EmotionResult`

**返回** (`EmotionResult.to_dict`):

```json
{
  "emotion": "happy",
  "intensity": 0.6,
  "category": "personal_sharing",
  "keywords": ["生日", "开心"],
  "summary": "用户对生日表达喜悦"
}
```

**失败行为**: `_compute_emotion` 捕获一切异常, 回退中性结果 `{"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""}` — 情绪分析失败不阻塞主流程。

**注意**: `extra_body={"enable_thinking": False}` 是 DashScope Qwen3 系列的特有开关, 用来阻止思考流打乱 JSON 输出——参见 [dev-decisions.md](../dev-decisions.md)。

---

## 4. `make_update_addressing_tool(memory_store, persona_id, user_id, actor_id=None)` (v0.2.10)

**签名**:

```python
def make_update_addressing_tool(
    memory_store: SqliteMemoryStore,
    persona_id: str,
    user_id: str,
    actor_id: str | None = None,   # v0.3.0: 溯源用
):
    @tool
    async def update_addressing(
        persona_addressing: str | None = None,
        user_addressing: str | None = None,
        context: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]: ...
    return update_addressing
```

**依赖**: 只需 `SqliteMemoryStore`——无外部 API 调用。`persona_id / user_id` 通过闭包 bind, Agent 无法跨用户 / 跨人格改写。v0.3.0 起 `persona_id` 从 state 读取 (不再硬编码 `"default"`), `user_id` 是 **effective_user_id** (绑组用户写到组关系上——称呼属于"这个人", 不属于某个平台账号); `actor_id` 记录触发更新的参与者, 仅随返回值供溯源, 不改变写入目标。

**用途**: 让**关系分析 Agent** 在检测到用户真诚请求改变称呼或关系背景时, 把新值写回 `relationships` 表 (v0.2.10 新增的 3 个 nullable 列: `persona_addressing / user_addressing / context`) 并留下审计日志。判断维度 (是否玩笑 / 场景扮演 / 引用他人 / 撤回信号) 由 [`relationship_analysis` 提示词](../../src/core/agents/prompts/defaults/relationship_analysis.md) 指导, 代码层只做兜底:

- `reason` 至少 10 字, 触发 `ValueError` → ReAct 循环让 Agent 重试
- 三字段全 `None` 直接 `ValueError`
- 相同值的字段会被 store 层跳过 (不重复写审计)

**执行流程** ([update_addressing.py](../../src/tools/update_addressing.py)):

1. 校验 `reason` 长度 + "至少一字段非 None"
2. `memory_store.get_relationship(persona_id, user_id)` 读取旧值
3. `memory_store.update_relationship_addressing(..., source="agent", reason=r)` 原子写表 + 逐字段审计日志
4. 再读一次拿新值, 返回 `{updated_fields, prev, current, audit_ids, actor_id}` 供 Agent 后续推理

**返回**:

```json
{
  "updated_fields": ["user_addressing"],
  "prev": {"persona_addressing": null, "user_addressing": "你", "context": null},
  "current": {"persona_addressing": null, "user_addressing": "小哥", "context": null},
  "audit_ids": [42],
  "actor_id": "actor_226a36a5..."
}
```

`prev` / `current` 中的 `null` 表示该字段未被覆盖, 沿用 `config.local.toml` 的 `[persona.relation.*]` 基线; 面板 `GET /panel/admin/relationship` 会把这些 `null` 展开为当前有效值 (见 [memory-system.md](memory-system.md))。

**注意**:
- **只观察用户消息, 不能因为"我上一轮回复用了新称呼"就认为已稳定** — 模型自己的输出是 prompt 回声, 不构成新证据
- 一次调用允许改多字段 (语义原子), 但每个字段会写独立一行 audit, 方便按字段回退
- `source` 参数由代码固定为 `"agent"`; 面板手动编辑走 `PUT /panel/admin/relationship`, store 层写 `source="manual"`
- **不给 memory_analysis Agent 绑定此 tool** — 保持事实提取与关系演化职责隔离

---

## 5. 节点内组装

真实组装点 [nodes.py](../../src/core/graph/nodes.py):

```python
# memory_analysis_node (v0.3.0)
retriever = MemoryRetriever(forwarder, vector_store, memory_store)
rel_for_addressing = await memory_store.get_relationship(
    state.get("persona_id", "default"), source_user)
tools = [
    make_vector_search_tool(retriever, _retrieval_context(state, rel_for_addressing)),
]
out = await run_memory_analysis(
    forwarder=forwarder, source_user=source_user,
    conversation=conversation, tools=tools, max_iterations=4,
    emotion_analysis=emotion_text,      # 来自 state["emotion_analysis"] 预计算
    persona_name=..., persona_addressing=..., user_addressing=..., relation_context=...,
)
```

```python
# relationship_analysis_node (v0.3.0)
out = await run_relationship_analysis(
    forwarder=forwarder,
    current_relationship=current_rel_str,
    conversation=conversation,
    tools=[
        make_update_addressing_tool(
            memory_store, state.get("persona_id", "default"), source_user,
            actor_id=state.get("actor_id"),
        ),
    ],
    max_iterations=2,
    emotion_analysis=emotion_text,
)
```

两个节点都有**非归属守卫**: `source_user` 为空时直接返回空结果, 不组装工具、不调用 LLM。

**依赖注入方向**: 节点持有 forwarder / stores → 构造工厂闭包 → 交给 ReAct 循环 (`src/core/agents/react_runner.py`) → 循环调 `bind_tools` + `tool_call` 拿结果。

---

## 6. 错误行为

| 工具 | 触发条件 | 处理 |
|------|---------|------|
| `vector_search` | rerank 端点 4xx/5xx | 内部 `try/except` 降级为纯 cosine, 不上抛 |
| `vector_search` | embedding 端点异常 | 直接抛 `UpstreamError`, ReAct 循环感知并终止 |
| `update_addressing` | `reason` 短于 10 字 / 三字段全 `None` | 抛 `ValueError`, ReAct 循环拿到错误消息后让 Agent 重试; 数据库层保证事务原子 |
| `analyze_emotion` (预计算) | JSON 解析失败 / 上游异常 | `_compute_emotion` 捕获并回退中性结果, 不阻塞主流程 |

---

## 7. 与其他模块

| 模块 | 关系 |
|------|------|
| [LangGraph 编排](langgraph.md) | 2 个工具工厂在 `memory_analysis_node` / `relationship_analysis_node` 内组装 |
| [身份子系统](identity.md) | `vector_search` 的 `retrieval_ctx` 来自身份解析; `update_addressing` 闭包绑定 effective_user_id + actor_id |
| [Forwarder](forward.md) | 所有远端调用 (embed / rerank / chat) 的唯一出口 |
| [消息处理](message-processing.md) | 主对话前置检索复用 `MemoryRetriever` (非工具形式) |
| [记忆系统](memory-system.md) | `update_addressing` 写 `relationships` + `relationship_audit_log`, 面板 `GET/PUT /panel/admin/relationship` 与之共享数据 |
| [配置](../configuration.md) | 模型选择走 `role_bindings` (v0.2.3 起, `[chat]/[embedding]/[rerank]` 已废弃) |

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-11 | 初始设计: 3 个工具 (向量检索 / 情绪 / 衰减) |
| v0.2.1 | 2026-07-15 | 与代码对齐: 全部改为 `make_*_tool()` 闭包工厂描述; 补 rerank 降级、`enable_thinking=False`、`current_state` 由 `DecayState.from_priority` 决定; 明确主对话不绑定工具 |
| v0.2.1 | 2026-07-16 | 新增第 4 个工具 `classify_sentence_type` (提示词清洗 Agent 专用); 提示词模板迁到 PromptStore, 占位符统一 `__TEXT__` + `.replace` |
| v0.2.3 | 2026-07-17 | 所有工具工厂改接收 `MultiForwarder`, 通过 `role=ModelType.{ASSIST,EMBEDDING,RERANK}` 从 `role_bindings` 拉候选; embedding 单绑定不 fallback (v0.2.4) |
| v0.2.6 | 2026-07-18 | 与代码对齐: 修正行号引用, 移除对 `settings.chat.assist_model` / `settings.rerank` 的引用 (v0.2.3 已废弃) |
| v0.2.10 | 2026-07-19 | 新增 `update_addressing` (关系分析 Agent 专用): 让 Agent 把用户消息中"以后叫我 X"等真诚请求落库到 `relationships.persona_addressing/user_addressing/context`, 同时写 `relationship_audit_log`; `persona_id/user_id` 通过闭包 bind 防跨用户改写 |
| v0.2.11 | 2026-07-19 | 文档补齐: 与 v0.2.7–v0.2.11 面板/后端变更一致 |
| v0.2.12 | 2026-07-25 | 移除 `time_decay_calculator` (衰减改确定性公式 `run_deterministic_decay`) 与 `sentence_classifier` (提示词清洗改单次重写); 情绪分析去重 |
| v0.3.0 | 2026-07-26 | `make_vector_search_tool` 新增 `retrieval_ctx` 参数 (两级受众过滤); 情绪分析从 ReAct 工具改为 `main_dialogue_node` 预计算 + state 共享; `make_update_addressing_tool` 的 `persona_id` 改从 state 读取并新增 `actor_id` 溯源参数; 迭代上限 memory 6→4 / relationship 3→2 |
| v0.3.4 | 2026-07-28 | 新增内部 tool 注册表 (`InternalToolRegistry`): 服务端内部工具注入主模型, 拦截执行, 不返回客户端; 首批内部 tool: `initiate_identity_binding` / `confirm_identity_binding` (跨平台身份绑定) |
