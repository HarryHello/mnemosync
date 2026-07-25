# Agent 简化与优化设计 | Agent Simplification & Optimization Design

> **文档版本**: v0.4.0-draft
> **创建时间**: 2026-07-25
> **最后更新**: 2026-07-25
> **状态**: 设计预留 · 未进入开发 (Design Reservation · No Implementation)

---

## 1. 文档目的

本文记录对 Mnemosync 当前 Agent 系统的分析结论和优化方向，基于对 Hermes Agent、Pi Agent、ChatLuna 的源码研究以及现有 Agent 的实际职责分析。

**核心原则**：让 LLM 做语义理解，让数学做计算，让确定性规则做过滤。

---

## 2. 当前 Agent 全景

| # | Agent | 推理方法 | 使用模型 | 触发时机 | 输出 |
|---|-------|---------|---------|---------|------|
| 1 | 主对话 | 直接推理 | 主模型 | 每次请求必跑 | 回复文本 |
| 2 | 代理思考 | CoT (无工具) | 辅助模型 | 主模型无原生推理 & 启用 | reasoning_content |
| 3 | 记忆分析 | ReAct | 辅助模型 | 主对话后，与关系分析并行 | new_memories + decay_evaluations |
| 4 | 关系分析 | ReAct | 辅助模型 | 主对话后，与记忆分析并行 | 关系更新建议 |
| 5 | 提示词清洗 | ReAct | 辅助模型 | 客户端 system 非空 | retained + discarded |

---

## 3. 问题分析

### 3.1 记忆衰减：LLM 在做数学题

当前记忆分析 Agent 在一次 ReAct 循环中做两件事：提取新记忆 + 评估已有记忆的衰减。

但衰减公式本质上是确定的：

```text
priority = importance × 0.5^(days/half_life) × expiration_penalty
         + log(access_count + 1) × 0.05
```

LLM 在衰减评估中的边际价值很小——调整衰减速度的判断，其输入（时间、重要性、访问频率）已经由公式覆盖。但成本不低——每次 ReAct 循环中多一轮 `time_decay_calculator` 工具调用。

**结论**：记忆衰减不需要 LLM。让数学做计算。

### 3.2 提示词清洗：逐句分类效率低且不严谨

当前提示词清洗 Agent 是 ReAct 循环：拆句 → 逐句调 `classify_sentence_type` 工具 → 工具内部再调一次 LLM → 汇总。10 句话 = 10+ 次辅助模型调用。

而且逐句分类处理不了混合句：

```text
"作为一个专业的客服，请用 JSON 格式回复"
         ↑ 人格                      ↑ 指令
```

逐句只能二选一，要么整句保留要么整句丢弃。

**结论**：提示词清洗不需要逐句 ReAct。单次 LLM 调用，直接重写。

### 3.3 Agent 缺乏统一运行契约

当前 5 个 Agent 各自独立实现，没有统一的超时、取消、Trace、版本化机制。如果 Memory Analysis 超时，没有标准化的失败记录；如果主对话已完成，后台 Agent 无法被取消。

---

## 4. 优化方案

### 4.1 记忆分析 Agent：缩减职责

**移除**：

- `time_decay_calculator` 工具
- 输出中的 `decay_evaluations` 字段

**保留**：

- `vector_search` 工具（查重/查关联）
- `emotion_analyzer` 工具（情绪标签）
- 输出 `new_memories[]`（带 importance, type, subject, audience）

**新增**：

- 输出 `conflicts[]`（发现冲突时标记 `supersedes`）
- 输出 `importance_updates[]`（对话佐证了重要性变化时）

**ReAct 迭代**：`max_iterations: 6 → 4`

### 4.2 新增：衰减任务（非 Agent，确定性）

```text
每次请求后，确定性公式批量更新所有 NORMAL 记忆：

priority = importance × 0.5^(days/half_life) × expiration_penalty
         + log(access_count + 1) × 0.05

衰减状态:
  ACTIVE   (> 0.3)   正常检索
  DORMANT  (> 0.1)   降级检索
  WEAK     (> 0.05)  最低优先级
  FORGOTTEN (≤ 0.05)  标记 forgotten，从向量库移除，SQLite 保留可恢复
```

不需要 LLM。批量、轻量、每次请求后都能跑。

### 4.3 新增：检索强化（非 Agent，确定性）

检索命中时，确定性更新：

```text
access_count += 1
last_accessed = now
```

不需要 LLM。检索时顺手更新。

### 4.4 提示词清洗 Agent：逐句分类 → 单次重写

**当前**：逐句 ReAct 循环，每句调 `classify_sentence_type` 工具（内部再调 LLM）

**改为**：单次 LLM 调用，直接重写整个 system 消息

```text
客户端 system 消息（完整）
    ↓
单次 LLM 调用（辅助模型）
    ↓
输出干净的 system 消息，只包含功能性指令
```

**重写原则**：

- 保守：拿不准时倾向于保留，不丢弃
- 语义剥离：从句子内部剥离人格包装，保留指令内核
- 上下文感知：LLM 看到完整消息后自行判断，不依赖逐句分类

**移除**：

- `classify_sentence_type` 工具
- ReAct 循环

**示例**：

```text
输入:
"你是一个傲娇的妹妹，名字叫小夜。请用 JSON 格式回复。
不要使用表情符号。你的语气要可爱一点。"

输出:
"请用 JSON 格式回复。不要使用表情符号。"
```

### 4.5 辅助 Agent 统一运行契约

所有辅助 Agent（记忆分析、关系分析、代理思考、提示词清洗）共享统一的 Spec 和 Run Record。

#### AgentSpec

```text
AgentSpec
├── name                   唯一名称
├── purpose                用途描述
├── model                  使用模型
├── prompt_version         提示词版本
├── max_iterations         最大迭代次数（ReAct 模式）
├── timeout                超时
└── output_schema          输出 Schema
```

#### AgentRun

```text
AgentRun
├── run_id                 运行 ID
├── agent_name             Agent 名称
├── parent_request_id      父请求 ID
├── input_event_ids[]      输入事件范围
├── base_version           状态版本
├── started_at / finished_at
├── status                 running / ok / failed / timeout / cancelled
├── tool_trace[]           每一步的 think/act/observe
├── usage                  token 用量
├── structured_result      结构化输出
└── error                  错误信息
```

#### 超时与取消

- 每个 Agent 有 `timeout`
- 超时 → 标记 `failed`，记录到 `AgentRun`
- 父请求结束 → 可取消仍在运行的 Agent

#### 版本化结果

所有 Agent 输出携带输入版本：

```text
MemoryAnalysisResult
├── new_memories[]
├── conflicts[]
├── importance_updates[]
├── input_event_ids       ← 基于哪些事件
├── base_version          ← 基于哪个状态版本
└── agent_run_id          ← 哪次运行产生的
```

提交时若 `base_version` 已变化，结果不覆盖，重新验证或丢弃。

---

## 5. 优化后的 Agent 全景

| # | Agent | 推理方法 | 使用模型 | 触发时机 | 输出 | 变化 |
|---|-------|---------|---------|---------|------|------|
| 1 | 主对话 | 直接推理 | 主模型 | 每次请求必跑 | 回复文本 | 不变 |
| 2 | 代理思考 | CoT (无工具) | 辅助模型 | 主模型无原生推理 & 启用 | reasoning_content | 不变 |
| 3 | 记忆分析 | ReAct | 辅助模型 | 主对话后，与关系分析并行 | new_memories + conflicts + importance_updates | 移除衰减评估，新增冲突和重要性更新 |
| 4 | 关系分析 | ReAct | 辅助模型 | 主对话后，与记忆分析并行 | 关系更新建议 | 不变 |
| 5 | 提示词清洗 | 单次调用 | 辅助模型 | 客户端 system 非空 | 重写后的 clean prompt | 从 ReAct 改为单次调用 |
| — | 衰减任务 | 确定性公式 | 无 | 每次请求后 | 更新 priority，标记 forgotten | 新增，非 Agent |
| — | 检索强化 | 确定性更新 | 无 | 检索命中时 | access_count, last_accessed | 新增，非 Agent |

---

## 6. 不做的事

- **不做动态 Agent 加载**：Agent 集合是固定的，不需要 Markdown 文件扫描、用户自定义
- **不做任务附着**：Mnemosync 是模型服务商，没有"用户把会话绑定到后台任务"的场景
- **不做触发器**：定时触发是平台适配器的事
- **不做权限系统**：Agent 是内部实现，工具集在代码中固定
- **不做暂停/恢复**：辅助 Agent 生命周期很短（几秒到几十秒）

---

## 7. 与外部项目的参考关系

| 参考 | 借鉴了什么 | 不借鉴什么 |
|------|-----------|-----------|
| **ChatLuna Sub-Agent** | AgentRun 记录、超时/取消、版本化 | 动态 Agent 定义、权限系统、触发器 |
| **Pi Agent** | 追加式事件、检查点版本化 | 会话树、分支 |
| **Hermes Agent** | MemoryProvider 生命周期 | 多 Provider 动态注册 |

---

## 8. 核心原则总结

```text
LLM 做语义    → 记忆提取、冲突发现、关系推断、提示词重写
数学做计算    → 衰减公式、检索强化、相似度
规则做过滤    → 受众过滤、身份验证、安全扫描
平台做触发    → 何时发言、@、关键词、活跃度
```

Mnemosync 的 Agent 系统不需要成为一个通用 Agent 平台。它只需要让现有的几个专用 Agent 更高效、更可观测、更可取消。