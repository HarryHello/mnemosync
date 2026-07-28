# 上游转发模块 (Forward Module)

> **模块版本**: v0.3.0
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-26
> **作者**: HarryHelloo

---

## 1. 定位

Forwarder 是 Mnemosync **所有上游 HTTP 调用的唯一出口** — 包括对话、嵌入、重排、模型列表。Mnemosync 本地不做推理; Agent 节点组装请求后, 一律通过它送到远端服务商。

v0.2.3 起, 顶层入口是 `MultiForwarder` — 它按角色 (`main`/`assist`/`embedding`/`rerank`) 从 `role_bindings` 拉出候选优先级列表, 用第一位 `ResolvedCandidate` 构造一个内部 `Forwarder` 实例发起调用; 失败时按候选顺序 fallback (**嵌入角色除外**, 见 §1.1)。

**代码位置**:
- [src/infra/forwarder/forwarder.py](../../src/infra/forwarder/forwarder.py) — `Forwarder` (单服务商)
- [src/infra/forwarder/multi.py](../../src/infra/forwarder/multi.py) — `MultiForwarder` (多候选调度)
- [src/infra/forwarder/connection_pool.py](../../src/infra/forwarder/connection_pool.py) — 连接池
- [src/infra/forwarder/debug_hook.py](../../src/infra/forwarder/debug_hook.py) — v0.2.5 出/入方向 emit 到 DebugEventBus

**调用方一览**:

| 调用者 | 方法 | 用途 | 角色 |
|-------|------|------|------|
| 主对话 Agent (非流式) | `MultiForwarder.chat()` | 生成回复 | main |
| 主对话 (流式路径 forward.py) | `MultiForwarder.chat_stream()` | SSE 透传给客户端 | main |
| 记忆分析 / 关系分析 / 代理思考 | `MultiForwarder.chat()` (含 tools) | ReAct 循环 | assist |
| MemoryRetriever / MemoryLifecycle | `MultiForwarder.embed()` | 文本 → 向量 | embedding |
| MemoryRetriever | `MultiForwarder.rerank()` | 检索精排 | rerank |
| LLM 服务管理 | `Forwarder.list_models()` | 拉取服务商模型列表 (直接实例, 不走 Multi) |

### 1.1 嵌入角色的特殊语义 (v0.2.4)

嵌入角色**只能有一条绑定**, 且 `MultiForwarder.embed()` 遇错**直接抛出, 不 fallback**。理由: 不同嵌入模型输出的向量空间语义不同, 换模型会让已存向量瞬间失效; ChromaDB collection 在首次写入时锁定 `(service_id, model, dim)` 三元组, 之后每次写入前 assert 一致, 不一致抛 `VectorStoreLockError`。想换模型必须走 `POST /panel/admin/memory/reindex`。见 [dev-decisions.md 嵌入模型单绑定 + Reindex + Prune](../dev-decisions.md)。

主/辅助/重排保持原多候选 fallback 语义不变。

---

## 2. 快速开始

生产代码不直接 `new Forwarder()` — 走 `MultiForwarder`:

```python
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

# resolver 由 lifespan/deps 注入, 从 role_bindings 读取候选
multi = MultiForwarder(resolver)
resp = await multi.chat(
    role=ModelType.MAIN,
    messages=[{"role": "user", "content": "你好"}],
)
```

低层 `Forwarder` 保留给需要精确控制服务商的场景 (如 CLI `probe-dimension`):

```python
from src.infra.forwarder import Forwarder, ForwarderConfig

config = ForwarderConfig(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-...",
    default_model="qwen-max",
    timeout=30.0,
)

async with Forwarder(config) as fwd:
    async for chunk in fwd.chat_stream(messages=[...]):
        ...
```

---

## 3. API

### 3.1 ForwarderConfig

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `base_url` | str | — | 服务商 OpenAI 兼容基址 |
| `api_key` | str | — | 服务商 API Key |
| `default_model` | str | — | `chat` / `chat_stream` 默认模型 |
| `timeout` | float | 30.0 | 请求超时 (秒) |
| `connect_timeout` | float | 10.0 | 连接超时 (秒) |

生产使用建议根据调用类型给不同超时: 主对话流式 `90s`, 记忆图内部 `30s` (见 [forward.py](../../src/api/routes/forward.py))。

### 3.2 Forwarder.chat

```python
async def chat(
    messages, model=None, temperature=1.0, max_tokens=None,
    tools=None, tool_choice=None,
    response_format=None, extra_body=None, **kwargs,
) -> dict
```

- 非流式对话, 返回完整 OpenAI 响应字典
- `tools` 用于 function_call (ReAct 循环)
- `extra_body` 合并到 payload 顶层, 如 `{"enable_thinking": False}` 关闭 Qwen3 思考流

**契约**: DashScope 等服务商 tools 与 stream=True 互斥, 但**这是服务商的限制**, 不是 Mnemosync 层的限制。`chat_stream` 支持透传 tools 等所有可选字段 (见 §3.3), 撞上服务商限制时上游会返回 4xx, 错误通过 SSE error 帧回到客户端。

### 3.3 Forwarder.chat_stream

```python
async def chat_stream(
    messages, model=None, temperature=1.0, max_tokens=None, **kwargs,
) -> AsyncIterator[bytes]
```

- 流式对话, yield 上游 SSE 原始字节 (`b"data: {...}\n\n"`)
- `**kwargs` 会原样合入 payload——**支持 tools / tool_choice / response_format / stream_options / top_p / stop / seed / reasoning_effort / thinking 等所有 OpenAI 兼容字段**
- 服务商侧限制 (如 DashScope stream+tools 互斥) 交由上游报错反馈, 本层不做静默剥离

### 3.4 Forwarder.embed

```python
async def embed(
    input: str | list[str], model: str, dimensions: int | None = None,
) -> list[list[float]]
```

- 调 `/embeddings`, 返回向量列表 (按 index 排序)
- `dimensions` 可选; 若不指定, 维度由所选模型决定 (见 [dev-decisions.md](../dev-decisions.md))

### 3.5 Forwarder.rerank

```python
async def rerank(
    query: str, documents: list[str], model: str, top_n: int | None = None,
) -> list[dict]
```

- 先试 `/rerank`, 404 时降级到 `/reranks` (部分服务商用复数)
- 返回 `[{index, relevance_score, document}, ...]`, 按 relevance_score 降序

### 3.6 Forwarder.list_models

```python
async def list_models() -> list[str]
```

调 `GET /models`, 返回模型 id 列表。

### 3.7 生命周期

- 支持 `async with`; 或手动 `await fwd.close()`
- 可选注入 `ConnectionPool` 复用底层 httpx client (见 [connection_pool.py](../../src/infra/forwarder/connection_pool.py))

---

## 4. 上游 API 契约

**已知约束**:

- DashScope OpenAI 兼容端点 tools 与 stream 互斥——**由服务商拒绝, Mnemosync 不预先剥离**; 客户端会收到 SSE error 帧告知具体错误
- rerank 端点在部分服务商为 `/reranks` (复数)
- DashScope 的 `gte-rerank` 已下线, 请用 `gte-rerank-v2` 或其他继任
- 嵌入维度由模型决定, 不再写死 (`dimensions` 只在服务商明确支持时传)

---

## 5. 错误

| 异常 | 触发 | 处理 |
|------|------|------|
| `UpstreamError(status, message)` | 4xx/5xx | 记 log, 上抛给节点 |
| `UpstreamTimeout(msg)` | httpx 超时 | 记 log, 上抛 |

Debug: 设 `MNEMOSYNC_DEBUG=1` 后, `chat` / `chat_stream` 会打印上游请求/响应到日志 (见 [chore commit `--debug` mode](../../src/infra/forwarder/forwarder.py))。

---

## 6. 与 API 层的关系

- **鉴权与身份解析 (v0.3.0)**: [src/api/routes/forward.py](../../src/api/routes/forward.py) 完成 API Key 验证 (`_verify_api_key`) 后, 立即通过 `_resolve_identity_context` 解析身份 (详见 [identity.md](identity.md))。身份策略绑定在 API Key 的 `strategy_id` 上, 解析结果 (`IdentityContext`) 提供 `actor_id` / `effective_user_id` / `space_id` / `channel_type` / `external_event_id`。解析失败或无策略时退化为**非归属模式**: 不创建 Actor, 不读写私有记忆, 回复仍正常工作。
- **幂等预检与重放 (v0.3.0)**: 在提示词清洗和上游调用之前, `_lookup_idempotency` 按 `(api_key.id, external_event_id)` 查幂等缓存。命中则直接重放首次响应 (`_replay_json_response` / `_replay_stream_response`), 零 LLM 开销、零记忆副作用。首次成功响应后通过 `_record_idempotency` 落库。
- **initial_state 注入 (v0.3.0)**: `create_chat_completion` 构建 initial_state 时注入 `actor_id` / `space_id` / `channel_type` / `persona_id` / `external_event_id` / `api_key_id`, 供下游节点和短期记忆装填使用。
- **source_frontend 派生**: 从 `api_key.note` 服务器派生 (`_resolve_source_frontend`), 不依赖客户端
- **模型白名单**: `/v1/chat/completions` 只接受 `model="mnemosync-any"` 或空, 其他直接 400
- **短期记忆装填 (v0.2.6)**: 主 Forwarder 调用前, forward.py 用 `main_candidate.context_length` 从 `conversation_turns` 双窗裁剪历史, 传入 `space_id` 做空间分区; 主对话结束后同步写 user + assistant 两条 turn, 传入 `actor_id` / `space_id` / `external_event_id`
- **受众过滤 (v0.3.0)**: 流式与非流式路径均构建 `RetrievalContext` (含 `effective_user_id` / `actor_id` / `space_id` / `channel_type` / `relationship`), 传给 `MemoryRetriever.search` 和 `AudienceFilter.filter` 做 ChromaDB `$or` 粗筛 + `is_visible` 精筛
- **代理推理**: 由 [src/api/reasoning_control.py](../../src/api/reasoning_control.py) 的决策函数控制。见 [agents.md](agents.md) §4
- **流式字段透传**: `_handle_stream` 会把 `request` 里的 OpenAI 兼容可选字段 (tools / tool_choice / response_format / top_p / seed / stream_options / reasoning_effort 等) 打包为 `passthrough` 传给 `MultiForwarder.chat_stream(**passthrough)`
- **客户端工具协议 (第一阶段)**: 流式与非流式主路径都会将客户端 `tools` / `tool_choice` 交给 MAIN；存在 `tools` 时同时透传 `parallel_tool_calls`。非流式使用 `MainDialogueResult` 保留完整 `message` / `finish_reason` / `usage`, 返回的 `tool_calls` 不再丢失。流式 SSE 继续原样返回客户端，同时 `parse_sse_stream_full` 按 `tool_calls[index]` 累积跨帧 `function.arguments`, 并保留 `finish_reason`。纯工具调用中间轮不会触发记忆与关系分析。
- **工具结果续轮**: 当请求以 `role=tool` 结尾时，核心只从最后一条 user 之后接纳连续的 `assistant(tool_calls) → tool` 事务尾部。校验包括：函数必须在本轮 `tools` 中、call ID 唯一且匹配、arguments 为 JSON 对象、所有并行调用都有结果、消息数和体积受限。合法事务接到服务器短期历史末端；其他客户端历史继续丢弃。工具续轮不重复写入根 user 事件、不复用根事件幂等键，也不启用代理推理。
- **API Key 工具策略**: 每个身份策略的 `tool_policy` 配置支持 `allowed_tools`（白名单）、`denied_tools`（黑名单）、`max_calls_per_round`（每轮最大调用数）和 `cooldown_seconds`（每工具冷却秒数）。策略对工具定义做入站过滤（模型不知道被禁工具的存在），对响应 `tool_calls` 做出站过滤（模型违反时作为最后防线移除）。被策略移除的工具调用不会到达客户端。
- **工具参数隐私检查**: 在响应返回客户端前，对 `tool_calls` 的每个参数执行确定性验证：工具名称必须在本轮 `tools` 中、arguments 必须是合法 JSON 对象、参数体积不超过 2000 字节、参数不得包含内部 UUID 格式（防止泄露内部 actor/group ID）。不符合检查的调用被移除并记录日志。
- **持久化冷却**: 内存中的冷却（单请求）从 `conversation_turns` 查询最近的 `tool_call` 事件，在跨请求/重启后仍生效。仅对配置了 `cooldown_seconds` 的工具生效，非流式路径完全有效，流式路径依赖内存冷却。
- **记忆治理端点**: `DELETE /panel/admin/memories` 支持按 `source_user`（必填）批量删除记忆，可选过滤 `memory_type` 和 `before`（ISO 时间）。已有单条删除端点 `DELETE /panel/admin/memories/{memory_id}`。`GET /panel/admin/memories` 新增 `before`/`after` 时间范围过滤。管理面板"长期记忆"tab 已增加批量删除按钮，按当前 source_user + 类型筛选条件批量删除。
- **工具策略管理**: 工具策略通过现有 identity strategy API 管理。在 identity strategy 的 `config` JSON 中添加 `tool_policy` 键即可配置白名单/黑名单/每轮上限/冷却，配置格式见 [forward.md](forward.md)。通过 `PATCH /panel/admin/identity/strategies/{id}` 更新后立即生效，无需重启。
- **模型候选工具能力**: `ResolvedCandidate` 增加 `supports_tools` / `supports_stream_tools` / `supports_parallel_tool_calls` / `supports_tool_choice_required` 字段（默认全部为 True）。当请求携带 `tools` 时，`RoleResolver.first_for_tools()` 优先选择支持工具的候选，不支持工具的候选跳过而非视为失败。流式请求额外要求 `supports_stream_tools=True`。
- **逻辑交互事务**: `interaction_id` 将同一根消息引发的多次 HTTP 请求（工具调用 → 工具结果 → 继续生成 → 最终文本）绑定为同一逻辑事务。根消息的 `request_id` 即 `interaction_id`；工具续轮通过首个 `tool_call_id` 查回该 ID。工具调用和结果分别作为 `event_type=tool_call` / `tool_result` 独立持久化，不混入自然语言流水。
- **幂等重放**: 幂等缓存现在保留完整 `response_message`（含 `tool_calls`）和 `finish_reason`，重放时优先恢复完整响应而不只是文本。纯工具调用响应可被正确重放。
- **仍有限制**: 上游候选工具能力声明尚未实现；工具续轮自身尚无幂等重放（仅根消息事件）。后续设计见 [群聊与工具演进](../design/group-chat-and-tool-evolution.md)。
- **调试面板 (v0.2.5)**: `debug_hook` 模块级单例被 lifespan 注入 `set_debug_bus(bus)`, 让 forwarder 每次出/入方向都写一条 event 到 DebugEventBus; 订阅数为 0 时 emit 走惰性 gate 近似 no-op

---

## 7. 连接池

```python
from src.infra.forwarder import ConnectionPool, Forwarder

pool = ConnectionPool(max_connections=50, max_keepalive_connections=10)
fwd = Forwarder(config, pool=pool)
...
await pool.close()
```

高并发场景使用; 单次请求不必要。

---

## 8. 服务商配置示例

**DashScope (阿里云百炼)**:
```python
ForwarderConfig(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx", default_model="qwen-max",
)
```

**SiliconFlow**:
```python
ForwarderConfig(
    base_url="https://api.siliconflow.cn/v1",
    api_key="sk-xxx", default_model="Qwen/Qwen2.5-72B-Instruct",
)
```

**OpenAI / OneAPI / Ollama** 同理, 只要接口 OpenAI 兼容即可。

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-29 | 初始实现: 转发, 连接池, SSE |
| v0.2.0 | 2026-07-12 | 定位为所有 Agent 的唯一上游通道 |
| v0.2.1 | 2026-07-14 | 迁移到 `src/infra/forwarder/`; API 改为 `chat` / `chat_stream` / `embed` / `rerank` / `list_models`, 移除旧 `send` / `send_stream` |
| v0.2.1 | 2026-07-15 | 与代码对齐: 修正 API 方法名、模型白名单说明、代理思考启用方式、rerank 端点降级 |
| v0.2.1 | 2026-07-15 | 流式路径全量透传 OpenAI 兼容可选字段 (tools / response_format / seed 等); 服务商限制由上游报错; 接入代理推理决策 (reasoning_control) |
| v0.2.3 | 2026-07-17 | 引入 `MultiForwarder` 顶层入口, 按角色 (main/assist/embedding/rerank) 从 `role_bindings` 拉候选; 非嵌入角色支持 fallback |
| v0.2.4 | 2026-07-17 | 嵌入角色单绑定, `MultiForwarder.embed()` 遇错不 fallback; ChromaDB collection 锁定 (service_id, model, dim) |
| v0.2.5 | 2026-07-17 | `debug_hook` 模块级单例; forwarder 出/入方向 emit 到 DebugEventBus |
| v0.2.6 | 2026-07-18 | forward.py 装填改由 `render_main_dialogue_system` + `build_short_term_history` 组合; 主对话完成后写 `conversation_turns` 两条 |
| v0.3.0 | 2026-07-26 | forward.py 新增身份解析、幂等预检/重放、initial_state 注入 actor_id/space_id/channel_type/persona_id/external_event_id/api_key_id; 记忆检索与短期记忆装填接入 space_id 分区与受众过滤 |
