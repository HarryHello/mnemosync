# 上游转发模块 (Forward Module)

> **模块版本**: v0.2.0
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-12
> **作者**: HarryHelloo

---

## 定位 (Positioning)

Forward 模块是 Mnemosync **所有模型调用的唯一 HTTP 出口**，也是与上游服务商交互的唯一通道。它不负责任何智能决策，只做 HTTP 转发、连接池管理、超时控制、SSE 流式处理。

**核心事实**：Mnemosync 本地不运行大模型，所有模型推理（包括 Agent 的"思考"）都发生在远端服务商。Agent 节点组装好请求后，统一通过 Forwarder 把请求送达服务商，再把模型响应拿回来。

```
Agent 节点（本地组装请求）
       │
       ▼
   Forwarder ─── HTTP ───→ 上游服务商（模型推理在这里发生）
       │                    OpenAI 兼容的任意模型服务商
       │  ←──── 响应（JSON 或 SSE 流）─────
       ▼
   解析响应 / 透传给前端 / 喂回 Agent 循环
```

**谁在用 Forwarder**：

| 调用者 | 调用方式 | 用途 |
|--------|----------|------|
| 主对话 Agent | 对话接口（`/chat/completions`） | 让主模型生成回复 |
| 记忆分析 Agent | 对话接口 + function_call | 驱动 ReAct 循环，模型自主调用工具 |
| 关系分析 Agent / 代理思考 Agent | 对话接口 | CoT 推理 |
| 向量检索 Agent | embedding / rerank 接口 | 生成向量、精排候选（非对话调用） |

> **注意**：Forwarder 不属于任何单个 Agent，它是模型调用基础设施。本模块的 API（`send` / `send_stream`）自 v0.1.0 以来保持稳定，v0.2.0 的变化只是**调用者从 Gateway/Pipeline 变为各 Agent 节点**。

---

## 概述

`forward` 模块负责将处理后的消息转发给上游模型提供商 (OpenAI/OneAPI/本地模型等).

**核心功能**:
- 发送请求到上游模型
- 支持流式 (SSE) 和非流式响应
- 连接池管理，复用 HTTP 连接
- 统一的错误处理

---

## 快速开始

### 基本用法

```python
from src.modules.forward import Forwarder, ForwarderConfig

# 1. 配置转发器
config = ForwarderConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-your-openai-key",
    default_model="gpt-4",
)

# 2. 创建转发器
forwarder = Forwarder(config)

# 3. 发送请求 (非流式)
messages = [
    {"role": "system", "content": "你是一个 AI 助手"},
    {"role": "user", "content": "你好"},
]

response = await forwarder.send(messages=messages)
print(response["choices"][0]["message"]["content"])

# 4. 发送请求 (流式)
async for chunk in forwarder.send_stream(messages=messages):
    print(chunk.decode(), end="")

# 5. 关闭连接
await forwarder.close()
```

### 使用连接池

```python
from src.modules.forward import Forwarder, ForwarderConfig, ConnectionPool

# 创建连接池
pool = ConnectionPool(
    max_connections=50,
    max_keepalive_connections=10,
)

# 创建转发器 (使用连接池)
forwarder = Forwarder(config, pool=pool)

# 使用完毕后关闭连接池
await pool.close()
```

### 上下文管理器

```python
async with Forwarder(config) as forwarder:
    response = await forwarder.send(messages=messages)
    # 自动关闭连接
```

---

## API 参考

### ForwarderConfig

转发器配置数据类.

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `base_url` | str | - | 上游模型基础 URL |
| `api_key` | str | - | 上游模型 API Key |
| `default_model` | str | - | 默认模型名称 |
| `timeout` | float | 30.0 | 请求超时 (秒) |
| `connect_timeout` | float | 10.0 | 连接超时 (秒) |

### Forwarder

上游转发器主类.

#### 方法

##### `send(messages, model, temperature, max_tokens, stream, **kwargs)`

发送请求到上游模型 (非流式).

**参数**:
- `messages`: list[dict] - 处理后的消息列表 (OpenAI 格式)
- `model`: str | None - 模型名称 (可选)
- `temperature`: float - 温度 (0-2), 默认 1.0
- `max_tokens`: int | None - 最大生成 token 数
- `stream`: bool - 是否流式
- `**kwargs`: 其他 OpenAI 兼容参数

**返回**: dict - 上游模型响应 (OpenAI 兼容格式)

**异常**:
- `UpstreamError`: 上游服务错误
- `UpstreamTimeout`: 上游超时

##### `send_stream(messages, model, temperature, max_tokens, **kwargs)`

发送请求到上游模型 (流式).

**参数**: 同 `send()`

**返回**: AsyncIterator[bytes] - SSE 格式的响应分块

**异常**: 同 `send()`

##### `close()`

关闭转发器和连接.

---

### ConnectionPool

HTTP 连接池.

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_connections` | int | 50 | 最大连接数 |
| `max_keepalive_connections` | int | 10 | 最大保持活跃的连接数 |
| `timeout` | float | 30.0 | 请求超时 |
| `connect_timeout` | float | 10.0 | 连接超时 |

---

## 错误处理

### UpstreamError

上游服务错误.

```python
from src.modules.forward import UpstreamError, Forwarder

try:
    response = await forwarder.send(messages=messages)
except UpstreamError as e:
    print(f"上游错误：{e.status_code} - {e.message}")
```

### UpstreamTimeout

上游服务超时.

```python
from src.modules.forward import UpstreamTimeout

try:
    response = await forwarder.send(messages=messages)
except UpstreamTimeout as e:
    print(f"上游超时：{e}")
```

---

## 配置示例

### OpenAI

```python
config = ForwarderConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
    default_model="gpt-4",
)
```

### OneAPI

```python
config = ForwarderConfig(
    base_url="https://your-oneapi.com/v1",
    api_key="sk-xxx",
    default_model="claude-3-opus",
)
```

### 本地模型 (Ollama)

```python
config = ForwarderConfig(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # Ollama 不需要 API Key
    default_model="qwen2.5:72b",
)
```

### SiliconFlow

```python
config = ForwarderConfig(
    base_url="https://api.siliconflow.cn/v1",
    api_key="sk-xxx",
    default_model="Qwen/Qwen2.5-72B-Instruct",
)
```

---

## 使用场景

### 场景 1: 简单转发

```python
# 接收处理后的消息，直接转发
response = await forwarder.send(
    messages=processed_messages,
    model="gpt-4",
    temperature=0.7,
)
```

### 场景 2: 流式响应

```python
# SSE 流式转发给前端
async def stream_to_client():
    async for chunk in forwarder.send_stream(
        messages=processed_messages,
        model="gpt-4",
    ):
        yield chunk
```

### 场景 3: 多模型路由

```python
# 根据配置选择不同上游
if config.provider == "openai":
    forwarder = Forwarder(openai_config)
elif config.provider == "local":
    forwarder = Forwarder(local_config)

response = await forwarder.send(messages=messages)
```

---

## 性能优化

### 连接池

```python
# 推荐配置
pool = ConnectionPool(
    max_connections=50,              # 根据并发量调整
    max_keepalive_connections=10,    # 保持活跃连接
)
```

### 超时设置

```python
# 根据模型响应时间调整
config = ForwarderConfig(
    timeout=60.0,         # 长文本生成需要更长时间
    connect_timeout=10.0, # 连接超时不宜过长
)
```

---

## 注意事项

1. **消息格式**: 传入的 `messages` 必须是 OpenAI 兼容格式
2. **流式响应**: 返回的是 bytes，需要前端按 SSE 格式解析
3. **连接管理**: 使用完毕后调用 `close()` 或使用上下文管理器
4. **错误处理**: 始终捕获 `UpstreamError` 和 `UpstreamTimeout`

---

## 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1.0 | 2026-03-29 | 初始实现：OpenAI 兼容转发、连接池、SSE 流式 |
| v0.2.0 | 2026-07-12 | 架构定位明确：成为所有 Agent 调用模型的唯一 HTTP 通道；API 不变 |
