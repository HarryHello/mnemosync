# 迭代计划 | Iteration Plan

> **文档版本**: v0.1.0
> **创建时间**: 2026-07-25
> **最后更新**: 2026-07-25
> **状态**: 计划中 · 待执行

---

## 1. 现状总结

基于对 Hermes Agent、Pi Agent、ChatLuna 的源码研究，已产出三份设计预留文档：

| 文档 | 路径 | 状态 |
|------|------|------|
| 单人格多用户群聊架构 | [docs/design/single-persona-multi-user-group-chat.md](single-persona-multi-user-group-chat.md) | 已完成 |
| 未来人格架构设计（六层模型） | [docs/design/future-persona-architecture.md](future-persona-architecture.md) | 已完成 |
| Agent 简化与优化设计 | [docs/design/agent-simplification.md](agent-simplification.md) | 已完成 |

这些文档为未来演进提供了架构边界和方向，但不承诺当前版本实现。当前阶段的核心任务是**在 v0.2.x 基础上，对已有 Agent 做减法：移除不必要的 LLM 调用，简化设计**。

---

## 2. 整体路线

```text
v0.2.x （当前）
    │
    ├── Phase 1: Agent 简化（v0.3.0）
    │   移除 LLM 衰减评估 → 确定性公式
    │   提示词清洗 ReAct → 单次重写
    │   其他 Agent 小幅优化
    │
    ├── Phase 2: Agent 统一化（v0.4.0）
    │   AgentSpec / AgentRun 契约
    │   超时、取消、版本化
    │
    └── Phase 3: 多用户基础（v0.5.0）
        Actor / Space 身份模型
        空间事件流和幂等
        受众过滤检索
```

---

## 3. Phase 1: Agent 简化（v0.3.0）

**目标**：让 LLM 做语义，让数学做计算，让规则做过滤。

**核心思路**：移除不需要 LLM 的职责（衰减评估、逐句分类），把 LLM 调用集中在真正需要语义理解的地方。

### 3.1 记忆分析 Agent：移除衰减评估

**当前**：

```text
记忆分析 Agent 在一次 ReAct 循环中做两件事：
1. 提取新记忆（需要 LLM）
2. 评估已有记忆衰减（调用 time_decay_calculator 工具 → 调 LLM）
```

**改为**：

```text
记忆分析 Agent 只做提取：
1. vector_search（查重/查关联）
2. emotion_analyzer（情绪标签）
3. 输出 new_memories[] + conflicts[] + importance_updates[]

记忆衰减由确定性公式在请求后批量执行。
```

**具体变更**：

- 移除 `time_decay_calculator` 工具
- 移除输出中的 `decay_evaluations` 字段
- 移除 Prompt 中的"第二部分：评估已有记忆衰减"和衰减速率表
- 输出新增 `conflicts[]`（冲突标记）和 `importance_updates[]`（重要性变化）
- `max_iterations`: 6 → 4

**涉及文件**：

- `src/core/agents/prompts/defaults/memory_analysis.md` — 清理衰减内容
- `src/core/agents/prompts/defaults/memory_analysis_decay_header.md` — 移除
- `src/core/agents/factory.py` — 移除衰减评估逻辑
- `src/core/graph/nodes.py` — 调整参数
- `src/tools/sentence_classifier.py` — 保留，提示词清洗仍需要（但改为单次重写方式）

### 3.2 新增：衰减任务（非 Agent）

**位置**：graph 节点或独立后台任务

**逻辑**：

```python
priority = importance × 0.5^(days/half_life) × expiration_penalty
         + log(access_count + 1) × 0.05

衰减状态:
  ACTIVE   (> 0.3)   正常检索
  DORMANT  (> 0.1)   降级检索
  WEAK     (> 0.05)  最低优先级
  FORGOTTEN (≤ 0.05)  标记 forgotten，从向量库移除，SQLite 保留可恢复
```

**触发时机**：每次对话请求后，轻量批量操作

**不涉及文件变更**：已有确定性衰减公式

### 3.3 新增：检索强化（非 Agent）

**逻辑**：检索命中时，确定性更新 `access_count += 1`, `last_accessed = now`

**位置**：检索流程中顺手更新

**不涉及文件变更**：已有 access_count 和 last_accessed 字段

### 3.4 提示词清洗 Agent：逐句分类 → 单次重写

**当前**：

```text
拆句 → 逐句调 classify_sentence_type 工具 → 工具内再调 LLM → 汇总
10 句话 = 10+ 次辅助模型调用
```

**改为**：

```text
单次 LLM 调用，直接重写整个 system 消息

输入: "你是一个傲娇的妹妹，名字叫小夜。请用 JSON 格式回复。"
输出: "请用 JSON 格式回复。"
```

**重写原则**：

- 保守：拿不准时倾向于保留，不丢弃
- 语义剥离：从句子内部剥离人格包装，保留指令内核
- 上下文感知：LLM 看到完整消息后自行判断

**具体变更**：

- 移除 `classify_sentence_type` 工具
- 移除 ReAct 循环
- Prompt 从分类指令改为重写指令
- `classify_sentence_type` 工具本身（`src/tools/sentence_classifier.py`）可以先保留，待提示词清洗迁移完成后再清理

**涉及文件**：

- `src/core/agents/prompts/defaults/prompt_cleaning_system.md` — 重写
- `src/core/agents/prompts/defaults/prompt_cleaning_user.md` — 不变（只替换占位符）
- `src/core/agents/prompts/defaults/sentence_classifier.md` — 可清理
- `src/core/agents/factory.py` — 改为单次调用
- `src/tools/sentence_classifier.py` — 可清理

### 3.5 关系分析 Agent：缩减迭代

**当前**：`max_iterations=3`

**改为**：`max_iterations: 3 → 2`

**涉及文件**：

- `src/core/agents/factory.py` — 修改参数

### 3.6 emotion_analyzer：去重

**当前**：

```text
memory_analysis:    调 emotion_analyzer → 情绪标签
relationship_analysis: 调 emotion_analyzer → 同一段对话的情绪标签
```

**改为**：在 graph 层预先计算一次情绪分析，结果注入两个 Agent 的输入

**涉及文件**：

- `src/core/graph/nodes.py` — 调整节点逻辑
- `src/core/agents/prompts/defaults/memory_analysis.md` — 移除工具调用指令
- `src/core/agents/prompts/defaults/relationship_analysis.md` — 移除工具调用指令

### 3.7 代理思考 Agent：中文化

**当前**：Prompt 为英文

**改为**：中文 Prompt，保持输出结构不变

**涉及文件**：

- `src/core/agents/prompts/defaults/proxy_thinking.md`

### 3.8 主对话 Agent：细化记忆使用指引

**当前**：

```text
自然地将对用户的了解融入对话，不要生硬地背诵记忆
```

**改为**：增加具体示例，区分永久记忆和检索记忆的使用方式

**涉及文件**：

- `src/core/agents/prompts/defaults/main_dialogue_frame.md`

---

## 4. Phase 1 文件变更清单汇总

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/core/agents/prompts/defaults/memory_analysis.md` | 修改 | 移除衰减内容，新增冲突/重要性指令 |
| `src/core/agents/prompts/defaults/memory_analysis_decay_header.md` | 删除 | 不再需要 |
| `src/core/agents/prompts/defaults/prompt_cleaning_system.md` | 修改 | 从分类改为重写 |
| `src/core/agents/prompts/defaults/sentence_classifier.md` | 删除 | 不再需要 |
| `src/core/agents/prompts/defaults/proxy_thinking.md` | 修改 | 英→中 |
| `src/core/agents/prompts/defaults/main_dialogue_frame.md` | 修改 | 细化记忆指引 |
| `src/core/agents/factory.py` | 修改 | 记忆分析移除衰减、提示词清洗改为单次、关系分析缩减迭代 |
| `src/core/graph/nodes.py` | 修改 | emotion_analyzer 提至 graph 层 |
| `src/tools/sentence_classifier.py` | 删除 | 不再需要 |

**新增文件**：

- 无。衰减任务和检索强化在现有代码中已有基础，不需要新增文件。

### 4.1 精简统计

| 指标 | 当前 | Phase 1 后 | 变化 |
|------|------|-----------|------|
| Agent 数量 | 5 | 5 | 不变（2 个非 Agent 任务） |
| LLM 模型工具 | `classify_sentence_type`, `time_decay_calculator`, `emotion_analyzer` | `emotion_analyzer`（共享） | 减少 2 个 |
| 提示词文件 | 8 个 | 6 个 | 减少 2 个 |
| 记忆分析迭代 | 6 | 4 | 减少 33% |
| 关系分析迭代 | 3 | 2 | 减少 33% |

---

## 5. Phase 2: Agent 统一化（v0.4.0）

**目标**：建立统一的辅助 Agent 运行契约，补齐超时、取消、Trace 和版本化。

### 5.1 实现 AgentSpec

```text
AgentSpec
├── name                   唯一名称
├── purpose                用途描述
├── model                  使用模型
├── prompt_version         提示词版本
├── max_iterations         最大迭代次数
├── timeout                超时
└── output_schema          输出 Schema
```

### 5.2 实现 AgentRun

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

### 5.3 超时与取消

- 每个 Agent 有 `timeout`
- 超时 → 标记 `failed`，记录到 `AgentRun`
- 父请求结束 → 可取消仍在运行的 Agent

### 5.4 版本化结果

所有 Agent 输出携带输入版本。提交时若 `base_version` 已变化，结果不覆盖，重新验证或丢弃。

---

## 6. Phase 3: 多用户基础（v0.5.0）

**目标**：为单人格多用户群聊打下身份和记忆作用域基础。

### 6.1 Actor / Space 身份模型

实现身份映射：

```text
integration_id      →  哪个适配器实例
platform            →  qq / telegram / discord / web
external_actor_id   →  平台侧用户标识
external_space_id   →  平台侧群/私聊标识
```

映射为内部：

```text
actor_id
space_id
```

### 6.2 空间事件流

实现 MessageEvent 的概念模型：

```text
event_id
actor_id
space_id
channel_type        group / direct
content
reply_to_event_id
external_event_id   用于幂等
committed_sequence  空间内顺序
```

### 6.3 受众过滤检索

实现 `subject` 和 `audience` 过滤，确保：

- 先按受众过滤，再把记忆交给模型
- 其他用户的私有记忆不进入群聊上下文
- 群内公开信息可以检索，但不可自动升级为跨群公共记忆

---

## 7. 不做的事

以下内容在当前迭代计划中明确不执行，未来也可能不做：

- 不做动态 Agent 加载：Agent 集合是固定的
- 不做任务附着：Mnemosync 不是 Agent 平台
- 不做触发器：定时触发是平台适配器的事
- 不做权限系统：Agent 是内部实现，工具集固定
- 不做暂停/恢复：辅助 Agent 生命周期很短
- 不修改客户端行为：中间件功能不依赖任何客户端配合
- 不定义自定义格式：回复内容遵从平台要求的格式
- 不要求平台适配：不可控的调用方不做特殊适配
- 不开发 AstrBot / ChatLuna 适配器

---

## 8. 优先级判断

| 任务 | Phase | 影响 | 风险 | 优先级 |
|------|-------|------|------|--------|
| 记忆分析移除衰减 | 1 | 减少一次 LLM 调用，简化 Agent | 中（需验证公式覆盖所有场景） | P0 |
| 提示词清洗改单次重写 | 1 | 大幅减少 LLM 调用 | 低（单次调用比逐句更简单） | P0 |
| 衰减任务（确定性） | 1 | 提高衰减频率和一致性 | 低（公式已有，只是执行方式变化） | P0 |
| 关系分析缩减迭代 | 1 | 微小优化 | 低 | P1 |
| emotion_analyzer 去重 | 1 | 减少一次 LLM 调用 | 低 | P1 |
| 代理思考中文化 | 1 | 一致性提升 | 低 | P2 |
| 主对话细化指引 | 1 | 质量提升 | 低 | P2 |
| 检索强化 | 1 | 提高记忆排序质量 | 低 | P2 |
| AgentSpec / AgentRun | 2 | 可观测性提升 | 中 | P2 |
| 超时/取消 | 2 | 稳定性提升 | 中 | P2 |
| 版本化结果 | 2 | 一致性提升 | 高 | P3 |
| Actor / Space 身份 | 3 | 多用户基础 | 高 | P3 |
| 受众过滤检索 | 3 | 隐私合规 | 高 | P3 |

---

## 9. 分阶段交付

### Phase 1（v0.3.0）

**预期减少**：

- 辅助模型调用：每轮请求减少 1-2 次（去重 + 移除两个工具）
- Prompt 文件：8 → 6 个
- Agent 迭代：33% 减少

**测试关注**：

- 记忆衰减公式是否覆盖了之前 LLM 处理的所有边界情况
- 提示词清洗单次重写是否比逐句分类更准确
- emotion_analyzer 共享后，两个 Agent 的情绪标签是否一致

### Phase 2（v0.4.0）

**预期改善**：

- Agent 运行可观测
- 超时不再导致整个请求挂起
- 版本化防止过期结果覆盖新状态

### Phase 3（v0.5.0）

**预期改善**：

- 多用户身份和记忆隔离
- 隐私受众强制执行
- 空间事件流和幂等

---

## 10. 核心原则总结

```text
LLM 做语义    → 记忆提取、冲突发现、关系推断、提示词重写
数学做计算    → 衰减公式、检索强化、相似度
规则做过滤    → 受众过滤、身份验证、安全扫描
平台做触发    → 何时发言、@、关键词、活跃度
客户端不可控  → 不依赖、不假设、不要求
格式不自定义  → 遵从平台要求，不创造协议
迭代要小      → 每次只改一件事，验证后再继续
```
