# 开发决策记录 | Development Decisions

> 本文档记录开发过程中遇到的问题、做出的决策、以及待你确认的事项.
> 你回来后请逐条过目, 标记同意或提出修改.

---

## 决策 1: Qwen3 thinking 模式与 JSON 输出的冲突

**日期**: 2026-07-12
**状态**: ✅ 已处理（待你确认方案）

### 问题

硅基流动的 `Qwen/Qwen3-8B` 默认开启 thinking 模式（输出 `<think>...</think>` 再给答案）。当使用 `response_format={"type": "json_object"}` 要求 JSON 输出时:
- 模型可能思考过久导致 30s 超时
- 返回的内容可能被 thinking 占用, 实际 JSON 字段为空（测试时 emotion=neutral, keywords=[]）

### 决策

1. **Forwarder.timeout 默认值从 30s 调到 60s**（给 thinking 留余量）
2. **emotion_analyzer / 记忆分析等需要结构化输出的 Agent, 在请求中传 `extra_body={"enable_thinking": False}`** 关闭 thinking
3. **主对话 Agent 保留 thinking**（输出质量更好, 且流式透传无影响）

### 你需要确认

- 是否同意关闭辅助 Agent 的 thinking?（关闭后响应更快, 但推理能力略降。对情绪分析/记忆提取这类结构化任务, 关闭更合适）
- 或者你想保留 thinking, 由我在解析时过滤 `<think>...</think>` 标签?

---

## 决策 2: 辅助模型选型

**日期**: 2026-07-12
**状态**: ✅ 已确认

### 背景

你最初填的 `assist_model = "Qwen/Qwen2.5-7B-Instruct"`. 测试发现它在硅基流动上**不支持 function_call**（探测脚本确认: Qwen2.5-7B 不发 tool_calls, 而是把调用当文本输出）.

支持 function_call 的候选: `Qwen/Qwen3-8B` / `Qwen/Qwen3-32B` / `Qwen/Qwen2.5-72B-Instruct`.

### 决策

按你的意愿"先期测试不需要太大模型", 改为 `assist_model = "Qwen/Qwen3-8B"`（与 main_model 同）.

### 你需要确认

- 后期若记忆分析 ReAct 循环不稳定, 再升级到 `Qwen3-32B` 作为辅助模型?

---

## 决策 3: 嵌入维度不写死

**日期**: 2026-07-12
**状态**: ✅ 已落地

按你的要求, 嵌入维度由所选模型决定, 不在代码里写死. `EmbeddingConfig.dimensions` 可为 `None`（用模型默认）或显式指定. ChromaDB collection 在首次 add 时由向量维度自动确定.

切换嵌入模型时需重新生成全量向量（已在 `llm-service.md` 文档化）.

---

## 决策 4: ReAct 实现风格

**日期**: 2026-07-12
**状态**: ✅ 已落地

采用**手写 ToolNode + 模型 function_call** 方案, 不用 LangGraph 的 `create_react_agent` 预构建.

理由:
- 便于在循环中加调试日志（Think-Act-Observe 每轮打印, 演示用）
- 灵活控制 max_iterations 和终止条件
- 能在 `state.errors` 中收集错误而非直接抛出

---

## 决策 5: 旧代码的处理

**日期**: 2026-07-12
**状态**: ✅ 已清理 (2026-07-14)

### 背景

v0.2 重构后, 曾一度存在两套并行代码:
- 旧: `src/accounts/` (auth + api_key), `src/modules/` (context/extraction/forward/memory), `src/storage/llm_service_store.py`, `src/models/llm_service.py`
- 新: `src/core/` + `src/infra/` + `src/persistence/` + `src/tools/`

CLI 与 API 已全部改用新结构, 旧目录处于 dead code 状态。这一割裂曾直接导致 bug: CLI 用的新版 `SqliteApiKeyStore` 忘了持久化 `key_full` 列, 导致 `show-key` 无法显示完整 key (旧版 `src/accounts/api_key_store.py` 反倒有正确实现, 却无人 import)。

### 决策

2026-07-14 一次性移除以下死代码:
- `src/accounts/`
- `src/modules/`
- `src/storage/`
- `src/models/`

同时清理若干 `__init__.py` 中未被使用的 re-export (`src/api/routes/__init__.py` / `src/api/schemas/__init__.py` / `src/persistence/__init__.py` / `src/infra/llm_service/__init__.py`) — 所有导入统一改为从子模块直取, 避免再次出现"两份实现互相错位"。

`src/persistence/api_key_store.py` 补齐 `key_full` 列与 `ALTER TABLE` 兼容语句, 修复上述 CLI bug。

---

## 待补充

后续遇到的新决策会追加到本文档.
