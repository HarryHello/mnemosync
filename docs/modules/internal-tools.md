# 内部工具与身份绑定 | Internal Tools & Identity Binding

> **模块版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-28
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 向主模型注入**内部工具** (Internal Tools), 与客户端提供的 `tools` 分离。模型调用内部 tool 时, Mnemosync 在出站拦截, 服务端执行, 合成 `tool_result`, 再调一轮 LLM 生成自然回复。客户端永远看不到内部 tool_calls。

**与 Agent 工具的区别**:

| 维度 | 内部工具 (Internal Tools) | Agent 工具 (ReAct) |
|------|--------------------------|-------------------|
| 注入对象 | 主模型 (main_dialogue_node) | 辅助 Agent (memory/relationship) |
| 拦截方式 | 出站过滤 + 服务端执行 | 闭包工厂 |
| 返回客户端 | 否 | 否 |
| 注册表 | `InternalToolRegistry` | 工具闭包绑定 |

**代码位置**:

- 注册表: [src/core/tools/internal_registry.py](../../src/core/tools/internal_registry.py)
- 身份绑定工具: [src/core/tools/identity_binding.py](../../src/core/tools/identity_binding.py)
- 出站拦截: [src/api/routes/forward/__init__.py](../../src/api/routes/forward/__init__.py)

---

## 2. InternalToolRegistry

### 2.1 InternalTool

```python
@dataclass(frozen=True)
class InternalTool:
    name: str                         # 唯一标识
    description: str                  # 人类可读描述
    parameters: dict[str, Any]        # JSON Schema
    handler: Callable[..., Awaitable[dict[str, Any]]]  # 异步处理函数
```

### 2.2 InternalToolRegistry

```python
class InternalToolRegistry:
    def register(tool: InternalTool) -> None
    def get(name: str) -> InternalTool | None
    def names -> set[str]              # 所有注册的 tool 名
    def to_openai_tools() -> list[dict]  # OpenAI tools 格式
    def is_empty() -> bool
```

全局单例通过 `get_internal_tool_registry()` / `set_internal_tool_registry()` 访问。

---

## 3. 请求处理流程

```
请求到达 (带 tools)
  │
  ▼
forward.py: 合并内部 tools + 客户端 tools
  │  tools = client_tools + registry.to_openai_tools()
  │
  ▼
上游 LLM 生成响应 (可能包含 tool_calls)
  │
  ▼
出站拦截: 分离内部 tool_calls 和客户端 tool_calls
  │  internal_calls = [tc for tc in tool_calls if tc.name in registry.names]
  │  client_calls = [tc for tc in tool_calls if tc.name not in registry.names]
  │
  ├─ 内部 tool_calls: 执行 handler, 合成 tool_result, 加入 messages
  │   └─ 再调一轮 LLM 生成自然回复
  │
  └─ 客户端 tool_calls: 原样返回给客户端
```

---

## 4. 已注册的内部工具

### 4.1 initiate_identity_binding

**用途**: 发起跨平台身份绑定。当用户表达了在不同平台是同一个人的意愿时调用。

**参数**: 无

**执行逻辑**:
1. 检查当前用户是否已识别 (actor_id)
2. 生成 6 位数字验证码
3. 存入内存 (TTL 5 分钟)
4. 返回验证码, 模型引导用户在另一端提供

### 4.2 confirm_identity_binding

**用途**: 确认跨平台身份绑定。用户提供了验证码时调用。

**参数**:
```json
{
  "code": "123456"  // 6 位数字验证码
}
```

**执行逻辑**:
1. 校验验证码有效性 (存在 + 未过期)
2. 检查不能与自身绑定
3. 检查当前用户未绑定到其他组
4. 绑定逻辑:
   - 目标有组 → 当前加入目标组
   - 双方无组 → 创建新组, 两人都加入
5. 返回绑定结果

---

## 5. 跨平台身份绑定流程

### 5.1 指令触发 (可靠)

```
用户 A (平台 1): "绑定"
  → 服务端拦截, 不调 LLM
  → 生成验证码 123456
  → 回复: "验证码: 123456, 请在另一端发送'绑定 123456'"

用户 B (平台 2): "绑定 123456"
  → 服务端拦截, 不调 LLM
  → 校验通过, 绑定到同一 UserGroup
  → 回复: "绑定成功"
```

### 5.2 自然语言触发 (增强)

```
用户 A: "我和 QQ 上的我是同一个人"
  → 模型理解意图, 调用 initiate_identity_binding
  → 服务端执行, 合成 tool_result
  → 模型生成自然回复: "好的, 验证码是 123456, 请在 QQ 端发送'绑定 123456'"

用户 B (QQ): "绑定 123456"
  → 模型调用 confirm_identity_binding(code="123456")
  → 服务端执行, 绑定成功
  → 模型生成自然回复: "绑定完成, 以后你在两个平台的记忆会共享"
```

---

## 6. 绑定码存储

`BindingCodeStore` (内存 dict + TTL):

| 属性 | 值 |
|------|-----|
| 存储方式 | 内存 dict |
| TTL | 5 分钟 |
| 码长度 | 6 位数字 |
| 并发安全 | asyncio.Lock |
| 过期清理 | 每次 generate/verify 时 |

---

## 7. 与其他模块

| 模块 | 关系 |
|------|------|
| [身份管理](identity.md) | 绑定结果写入 `actor_group_memberships` 表 |
| [内部工具注册表](#) | 工具通过 `InternalToolRegistry` 注册 |
| [主对话节点](langgraph.md) | `main_dialogue_node` 拦截内部 tool_calls |
| [Forward 转发](forward.md) | 出站过滤 + 重调逻辑 |

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.3.3 | 2026-07-28 | 初始版本: InternalToolRegistry + InternalTool; 身份绑定两个内部 tool; BindingCodeStore |
| v0.3.4 | 2026-07-30 | 出站拦截 + 重调逻辑; 自然语言触发支持 |
