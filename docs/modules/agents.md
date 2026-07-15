# 多 Agent 设计 | Multi-Agent Design

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-11
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 一次请求由 LangGraph 编排 **4 个 Agent** 完成。代理推理是**原生推理的补齐**, 主模型无原生推理且前台点名要推理时自动启用 (详见 §4)。

### 1.1 Agent 全景

| # | Agent | 推理方法 | 使用模型 | 触发时机 | 输出 |
|---|-------|---------|---------|---------|------|
| 1 | 主对话 | 直接推理 | 主模型 | 每次请求必跑 | 回复文本 |
| 2 | 代理推理 | CoT (无工具) | 辅助模型 | 主模型无原生推理 & (前台点名推理 或 `proxy_thinking_default=true`) 时启用 | 供主对话参考的思考文本 + 前台 `reasoning_content` 字段 |
| 3 | 记忆分析 | ReAct | 辅助模型 | 主对话后, 与关系分析并行 | 新记忆候选 + 衰减评估 JSON |
| 4 | 关系分析 | ReAct | 辅助模型 | 主对话后, 与记忆分析并行 | 亲密度/信任度增量 JSON |

**代码位置**: 所有 Agent 的执行函数集中在 [src/core/agents/factory.py](../../src/core/agents/factory.py); ReAct 循环由 [src/core/agents/base.py](../../src/core/agents/base.py) 的 `run_react_loop` 驱动。

### 1.2 LangGraph 拓扑

```
parse_request
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

**要点**:
- `parse_request` 不是 Agent, 是纯 Python 预处理节点 (提取新消息 + 用户标识)
- `relationship_analysis` 和 `memory_analysis` 是**并行边**, 主对话完成后同时触发
- 向量索引 (嵌入写入 Chroma) 在 `memory_analysis` 节点内部由 `MemoryLifecycle.store_candidate()` 顺手完成, **不是独立节点**
- **流式路径不经过图**: [forward.py `_handle_stream`](../../src/api/routes/forward.py) 在 API 层直接编织 "加载记忆 → 代理推理 (可选, 同步) → 合成 reasoning SSE 帧 → 上游 chat_stream 透传 → 后台记忆图", 图仅用于后台跑记忆/关系两个分析节点, 主对话与代理推理在 API 层就完成。见 [forward.md](forward.md) §6

### 1.3 与嵌入/重排模型的关系

嵌入模型 (embedding) 和重排模型 (rerank) 是**基础设施工具**, 不是 Agent。它们由 `MemoryRetriever` 和 `MemoryLifecycle` 直接调用, 完成"文本 ↔ 向量"的数学变换和候选精排, 没有 prompt / 推理 / 循环。

---

## 2. 主对话 Agent

**代码**: [factory.py:122 `run_main_dialogue`](../../src/core/agents/factory.py#L122)

### 2.1 职责

- 加载永久记忆 + 语义检索的相关记忆 + 关系状态
- 拼装人格 prompt + 记忆上下文 + 会话历史
- 调用主模型生成回复 (非流式路径) 或转发流式响应 (流式路径)

### 2.2 上下文拼装

调 [`build_main_dialogue_messages`](../../src/core/memory/context.py) 生成 OpenAI 格式的 messages:

```
[0] system  ─ persona_prompt + user_name + 关系状态 + 永久记忆 + 检索记忆
              + (可选) proxy_thinking_result
[1..N] user/assistant ─ 会话历史 (去掉原始 system)
```

### 2.3 推理

直接一次 chat completion, 无工具, 无循环。工具检索发生在**节点内部**的 Python 逻辑 (调 `MemoryRetriever.search`), 不通过 function call。

### 2.4 参数

- `temperature`: 0.7 (默认)
- `model`: `settings.chat.main_model`

---

## 3. 记忆分析 Agent

**代码**: [factory.py:137 `run_memory_analysis`](../../src/core/agents/factory.py#L137)
**Prompt**: [prompts/memory_analysis.py](../../src/core/agents/prompts/memory_analysis.py)

### 3.1 职责

- 从本轮对话中提取值得长期保存的信息 (`new_memories`)
- 评估一批已有普通记忆的衰减状态 (`decay_evaluations`)

两件事在同一次 ReAct 循环中完成; v0.2.0 曾计划把衰减评估拆成独立 Agent, 后合并。

### 3.2 ReAct 工具

| 工具 | 工厂函数 | 用途 |
|------|---------|------|
| `vector_search` | `make_vector_search_tool` | 检索已有记忆判断重复/冲突/关联 |
| `emotion_analyzer` | `make_emotion_analyzer_tool` | 分析对话情绪标签和强度 |
| `time_decay_calculator` | `make_time_decay_calculator_tool` | 计算已有记忆的理论衰减优先级 |

工具通过工厂注入依赖 (Forwarder / VectorStore / MemoryStore), 见 [tools.md](tools.md)。

### 3.3 循环约束

- `max_iterations = 6` (由 nodes.py 传入)
- 提示词要求: 先 `vector_search` 查重 → `emotion_analyzer` 定情绪 → 有衰减目标时 `time_decay_calculator` → 输出 JSON
- Agent 判断无需工具调用时直接输出 JSON, 循环终止

### 3.4 输出 JSON schema

```json
{
  "new_memories": [
    {
      "content": "用户对花生过敏",
      "memory_type": "PERMANENT",
      "importance": 1.0,
      "decay_rate": 0.0,
      "emotional_tags": ["health"],
      "expires_at": null,
      "overrides": null,
      "related_to": [],
      "reasoning": "健康信息, 必须永久记忆"
    }
  ],
  "decay_evaluations": [
    {
      "memory_id": "mem_xyz",
      "current_priority": 0.23,
      "new_priority": 0.30,
      "decision": "ACTIVE",
      "factors": {"time_factor": 0.23, "access_bonus": 0.02},
      "reflection": "情绪事件, 手动保留"
    }
  ]
}
```

字段解析由 `_parse_candidate` / `_parse_decay_eval` 完成, 未识别的枚举值回退到 `MemoryType.NORMAL` / `DecayState.ACTIVE`。

### 3.5 衰减速率参考

| decay_rate | 半衰期 | 场景 |
|-----------|--------|------|
| 0.0 | 永不过期 | 永久记忆 |
| 0.05 | ~182天 | 长期偏好 |
| 0.1 | ~91天 | 一般偏好、事实 |
| 0.3 | ~33天 | 中期事件、计划 |
| 0.7 | ~17天 | 短期事件 |
| 0.9 | ~11天 | 临时信息、情绪波动 |

### 3.6 衰减评估决策规则

| 调整后优先级 | decision |
|-------------|---------|
| > 0.3 | ACTIVE |
| 0.1 - 0.3 | DORMANT |
| 0.05 - 0.1 | WEAK |
| < 0.05 | FORGOTTEN |

---

## 4. 代理推理 Agent

**代码**: [factory.py:226 `run_proxy_thinking`](../../src/core/agents/factory.py#L226) · 决策与 SSE 合成: [src/api/reasoning_control.py](../../src/api/reasoning_control.py)
**Prompt**: [prompts/proxy_thinking.py](../../src/core/agents/prompts/proxy_thinking.py)

### 4.1 定位 (⚠️ 与直觉相反, 请仔细看)

代理推理是**原生推理的补齐 / 替代**, 不是可选优化。核心语义:

> "前台点名要推理" 是**必须提供推理**的信号 — 由原生或代理**任一**满足。
>
> 若主模型不具备原生推理却收到 `reasoning_effort` / `reasoning` / `thinking` 参数, Mnemosync **必须**启动代理推理去补齐, 否则前台永远看不到"思考"面板内容。

这与常见的"用户显式要求就跳过我们的层"直觉相反。原因: 客户端 (Cherry Studio / ChatBox 等) 在启用"深度思考"开关后会带上 `reasoning_effort`, 用户期待看到思考过程 — 如果我们此时静默 skip, 用户会认为"Mnemosync 破坏了思考功能"。

### 4.2 决策规则

由 [`should_use_proxy_thinking()`](../../src/api/reasoning_control.py) 判定, 优先级由上至下:

| # | 条件 | 结果 |
|---|------|------|
| 1 | 请求带 `tools` | **skip** (工具调用轮次多, 叠推理延迟不划算) |
| 2 | 主模型具备原生推理 (前缀表命中 或 自适应缓存) | **skip** (让原生接管) |
| 3 | 前台请求体带 `reasoning_effort` / `reasoning` / `thinking` | **enable** (补齐必须的推理) |
| 4 | fallback | 用 `[graph].proxy_thinking_default` |

**原生推理识别双通道**:
- 静态前缀表: `[graph].proxy_thinking_native_reasoning_models` (默认含 `o1*` / `o3*` / `o4*` / `deepseek-r1*` / `deepseek-reasoner*` / `qwen3-*-thinking` / `qwq*` / `gpt-5-thinking-*`)
- 自适应缓存: 流式路径观察到上游 chunk 含 `"reasoning_content"` 字段 → 记入进程内 `_native_cache` → 下次同模型自动 skip (进程重启后自动重学)

### 4.3 前台输出协议

代理推理的结果通过 OpenAI 兼容的 `reasoning_content` 字段回吐 (DeepSeek 首创, Cherry Studio / ChatBox / LibreChat / OpenWebUI 等均支持):

- **非流式**: `response.choices[0].message.reasoning_content` = 推理全文
- **流式**: 上游正文流之前先注入合成的 SSE 帧, 逐段 `delta.reasoning_content`, 然后是正常的 `delta.content` 流

客户端渲染折叠"思考"面板, 与真原生推理模型 (DeepSeek-R1 等) 行为一致。

### 4.4 双通道注入

同一份 `reasoning_text` 一份数据两个用途:

1. **注入主对话 prompt**: 通过 `build_main_dialogue_messages(proxy_thinking_result=...)` 作为"## 思考辅助"段拼进 system prompt, 让主模型基于该分析生成更好的回复
2. **回吐前台**: 作为 `reasoning_content` 字段/帧给客户端展示

### 4.5 工具

`run_proxy_thinking` 接受可选 `tools` 参数:
- `tools=None` (当前 nodes.py 传法) → 走 `run_simple_completion` 单次调用, 关闭 thinking, 无工具
- `tools=[...]` → 走 `run_react_loop`, 支持在循环中调工具

Prompt 里已注入永久记忆和关系状态, 通常无需再检索。

### 4.6 输出格式

模型自由文本 (非 JSON), 结构如下:

```
### 1. User Intent
### 2. Background Connection
### 3. Emotion Analysis
### 4. Response Strategy
```

主对话节点通过 `state["proxy_thinking_result"]` 读取此字符串, 拼进 system prompt。

### 4.7 失败降级

代理推理抛异常 → `reasoning_text=None` → 不合成帧, 不注入 prompt, 正常转发主对话流 → 用户端等同于未启用。记 warning, 不阻塞主对话。

---

## 5. 关系分析 Agent

**代码**: [factory.py:190 `run_relationship_analysis`](../../src/core/agents/factory.py#L190)
**Prompt**: [prompts/relationship_analysis.py](../../src/core/agents/prompts/relationship_analysis.py)

### 5.1 职责

从本轮对话中量化亲密度 / 信任度增量, 更新 `RelationshipState`。

### 5.2 循环与工具

- 走 `run_react_loop`, `max_iterations = 3`
- 唯一工具: `emotion_analyzer` (通过工厂函数注入 Forwarder)
- 提示词流程: 调 `emotion_analyzer` → 识别关系信号 → 量化 → 输出 JSON

### 5.3 信号量化参考

| 信号 | 亲密度影响 |
|------|-----------|
| 称呼变亲昵 | +0.05 ~ +0.10 |
| 隐私分享 | +0.10 ~ +0.20 |
| 情感表达 | +0.05 ~ +0.15 |
| 互动频率 | +0.01/天 |
| 长期沉默 (>30 天) | -0.01/天 |
| 距离信号 | -0.10 ~ -0.20 |

关系类型阈值: `<0.2 stranger`, `0.2-0.5 acquaintance`, `0.5-0.8 friend`, `>0.8 intimate`。

### 5.4 输出 JSON schema

```json
{
  "signals_detected": [{"type": "name_change", "detail": "...", "impact": 0.15}],
  "intimacy_delta": 0.23,
  "trust_delta": 0.10,
  "new_relationship_type": "friend",
  "notes": "...",
  "reasoning": "..."
}
```

`RelationshipAnalysisOutput` 只消费 `intimacy_delta / trust_delta / new_relationship_type / notes / reasoning`; `signals_detected` 用于日志观察, 不落库。

### 5.5 提示词构建

**必须**用 [`build_relationship_analysis_prompt`](../../src/core/agents/prompts/relationship_analysis.py) (内部用 `str.replace` 填占位符), **不能**用 `str.format`——prompt 里含字面 JSON, `.format()` 会把 `{"signals_detected"}` 当占位符抛 `KeyError`。参见 [dev-decisions.md](../dev-decisions.md)。

---

## 6. AgentState (共享状态)

**代码**: [src/core/graph/state.py](../../src/core/graph/state.py)

```python
class AgentState(TypedDict, total=False):
    # 请求上下文 (parse_request 写入)
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str
    persona: str
    persona_name: str
    thread_id: str
    proxy_thinking_enabled: bool

    # 代理推理 (proxy_thinking 写入)
    proxy_thinking_result: str | None

    # 主对话输出 (main_dialogue 写入)
    response: str
    response_chunks: list[bytes]

    # 记忆分析输出 (memory_analysis 写入)
    new_memories: list[dict]
    decay_evaluations: list[dict]
    decay_targets: list[dict]

    # 关系分析输出 (relationship_analysis 写入)
    relationship_delta: dict

    # 全局
    errors: list[str]
    stream_mode: bool
```

**注意**: 检索出的记忆 (`retrieved_memories` / `permanent_memories`) **不放入 state**, 由 `main_dialogue_node` 内部处理; 状态尽量瘦身以减少 checkpoint 开销。

---

## 7. 错误处理约定

- 单个 Agent 失败不影响并行分支; 每个 node 都用 try/except 包住, 失败时写 `state.errors`
- 记忆分析失败 → 跳过入库, 关系分析继续
- 关系分析失败 → 跳过关系更新, 主对话回复照常返回
- 代理推理失败 → 记 warning, 退化为无代理推理模式 (继续主对话, `reasoning_content` 为空)

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-11 | 初始多 Agent 设计 |
| v0.2.1 | 2026-07-12 | 记忆衰减合并入记忆分析; 新增代理推理 Agent |
| v0.2.1 | 2026-07-15 | 与代码对齐: 修正拓扑 (无 vector_index 节点), 修正 AgentState 字段, 删除通识讲解 |
| v0.2.1 | 2026-07-15 | 代理推理落地: API 层决策 (reasoning_control), 双通道注入 (system prompt + `reasoning_content` SSE 帧); 语义修正为"补齐原生推理"而非可选优化 |
