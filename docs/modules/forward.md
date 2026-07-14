# 上游转发模块 (Forward Module)

> **模块版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 定位

Forwarder 是 Mnemosync **所有上游 HTTP 调用的唯一出口**——包括对话、嵌入、重排、模型列表。Mnemosync 本地不做推理; Agent 节点组装请求后, 一律通过它送到远端服务商。

**代码位置**: [src/infra/forwarder/](../../src/infra/forwarder/) (`forwarder.py` / `connection_pool.py` / `errors.py`)。

**调用方一览**:

| 调用者 | 方法 | 用途 |
|-------|------|------|
| 主对话 Agent (非流式) | `chat()` | 生成回复 |
| 主对话 (流式路径 forward.py) | `chat_stream()` | SSE 透传给客户端 |
| 记忆分析 / 关系分析 / 代理思考 | `chat()` (含 tools) | ReAct 循环 |
| MemoryRetriever / MemoryLifecycle | `embed()` | 文本 → 向量 |
| MemoryRetriever | `rerank()` | 检索精排 |
| LLM 服务管理 | `list_models()` | 拉取服务商模型列表 |

---

## 2. 快速开始

```python
from src.infra.forwarder import Forwarder, ForwarderConfig

config = ForwarderConfig(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-...",
    default_model="qwen-max",
    timeout=30.0,
)

async with Forwarder(config) as fwd:
    resp = await fwd.chat(
        messages=[{"role": "user", "content": "你好"}],
    )
    print(resp["choices"][0]["message"]["content"])

    async for chunk in fwd.chat_stream(messages=[...]):
        # chunk 是 SSE 原始字节
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

生产使用建议根据调用类型给不同超时: 主对话流式 `90s`, 记忆图内部 `30s` (见 [forward.py:244/286](../../src/api/routes/forward.py))。

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

**契约**: DashScope 等服务商 tools 与 stream=True 互斥, 因此 `chat_stream` 不支持 `tools` (见 [dev-decisions.md](../dev-decisions.md))。

### 3.3 Forwarder.chat_stream

```python
async def chat_stream(
    messages, model=None, temperature=1.0, max_tokens=None, **kwargs,
) -> AsyncIterator[bytes]
```

- 流式对话, yield 上游 SSE 原始字节 (`b"data: {...}\n\n"`)
- 不接受 `tools` 参数

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

**已知约束** (见 [dashscope 记录](../../DASHSCOPE_CONTRACTS_NEEDED.md) 与 memory 索引):

- DashScope OpenAI 兼容端点 tools 与 stream 互斥
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

- **鉴权与请求组装**: [src/api/routes/forward.py](../../src/api/routes/forward.py) 完成 API Key 验证、模型白名单校验 (`mnemosync-any`)、记忆加载、上下文拼装, 再交给 Forwarder
- **模型白名单**: `/v1/chat/completions` 只接受 `model="mnemosync-any"` 或空, 其他直接 400 ([forward.py:131](../../src/api/routes/forward.py#L131))
- **代理思考**: 当前**不通过请求头启用**, `initial_state.proxy_thinking_enabled = False` 硬编码, 需修改代码启用

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
