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
**状态**: ⚠️ 待你确认

### 现状

旧目录 `src/accounts/` / `src/modules/` / `src/storage/` / `src/models/` / `src/api/schemas/` / `src/api/routes/` / `src/cli/` 仍在工作区（已 git 跟踪）, 但新代码全部写在 `src/core/` / `src/infra/` / `src/persistence/` / `src/tools/` / `src/api/`（新）.

### 决策（自主选择）

**暂时保留旧代码不删**, 理由:
- 旧代码（forwarder/auth/api_key/llm_service_store）的某些实现已被迁移到新结构并改进, 旧文件留着可对照参考
- 等新代码端到端跑通后, 一次性删除旧目录, 避免 mid-development 破坏 import

### 你需要确认

- 端到端跑通后是否同意我删除旧的 `src/accounts/` `src/modules/` `src/storage/` `src/models/` `src/api/schemas/`?

---

## 待补充

后续遇到的新决策会追加到本文档.
