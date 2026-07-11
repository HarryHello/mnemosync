# 多 Agent 设计 | Multi-Agent Design

> **系统版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-07-11
> **最后更新**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

本文档描述 Mnemosync 的 5 个 Agent 的详细设计，包括 prompt 模板、推理循环、工具绑定和输出格式。

### 1.0 执行模型（重要）

> **本地不运行大模型。** Agent 的"思考"发生在远端模型服务商，本地只负责组装请求、驱动循环、解析响应。

每个 Agent 的本质是：

```
Agent 节点（本地）：
  1. 拼 prompt + 注入 state 上下文
  2. 序列化工具定义（LangChain Tool → function schema）
  3. 通过 Forwarder 将请求送到模型服务商
  4. 解析模型返回：
     - 若是 function_call → 执行对应工具 → 把结果喂回模型 → 回到步骤 3
     - 若是最终输出 → 写入 state，结束本节点
  5. （ReAct/CoT 的多轮循环由此驱动）
```

因此本文中所有"Agent 判断""Agent 调用工具""Agent 输出"的表述，实际含义都是：**本地组装请求 → 远端模型推理决策 → 本地解析并执行**。Forwarder 是所有 Agent 调用模型的共用 HTTP 通道。

### 1.1 Agent 全景

| # | Agent | 推理方法 | 工具 | 触发时机 |
|---|-------|----------|------|----------|
| 1 | **主对话 Agent** | 直接推理 | 向量检索工具 | 每次用户请求 |
| 2 | **记忆分析 Agent** | ReAct | 向量检索工具、情绪分析工具、时间衰减计算工具 | 对话完成后（异步） |
| 3 | **代理思考 Agent** | CoT | 向量检索工具、情绪分析工具 | 用户启用时，在主对话前执行 |
| 4 | **向量检索 Agent** | 无（工具执行） | embedding + rerank API | 被其他 Agent 调用 |
| 5 | **关系分析 Agent** | CoT | 情绪分析工具 | 对话完成后（异步，并行） |

### 1.2 LangGraph 编排拓扑

```
                          用户启用代理思考?
                        /        \
                      是          否
                      /            \
                     ↓              ↓
          代理思考 Agent (CoT)      跳过
                      \            /
                       \          /
                        ↓        ↓
START → parse_request → main_dialogue ──→ memory_analysis ──→ vector_index → END
                              │                │
                              │                └──→ relationship_analysis ──→ vector_index
                              │
                              └──（流式返回，不等待后续节点）
```

**说明**：代理思考 Agent 是可选的——用户通过请求参数（如 `X-Enable-Proxy-Thinking: true`）启用。启用时，它在主对话 Agent 之前执行，将推理结果注入上下文。

---

## 2. Agent 1: 主对话 Agent (Main Dialogue Agent)

### 2.1 职责

- 加载人格 system prompt
- 调用向量检索 Agent 获取相关记忆
- 加载永久记忆列表
- 加载用户关系状态
- 拼装完整上下文（含代理思考结果，如启用）
- 调用主模型生成回复
- 流式透传回复给用户

### 2.2 推理方法

**直接推理**（大模型自身的推理能力）。主对话 Agent 本身不执行显式的推理循环（无 ReAct/CoT 驱动）— 它把人格 prompt + 记忆 + 当前消息组装好，通过 Forwarder 交给主模型，由模型一次性生成回复。模型内部的隐式推理即视为"直接推理"。

> **代理思考模式**：当用户启用代理思考时，代理思考 Agent 会在主对话之前完成显式 CoT 推理，将推理过程注入主对话 Agent 的 system prompt 中。此时主对话可改用参数较小的模型（辅助模型替代主模型），降低 token 成本。

### 2.3 工具

| 工具 | 调用方式 | 说明 |
|------|----------|------|
| `vector_search` | Function Call | 以当前消息为 query，embedding 语义检索相关长期记忆 |

### 2.4 System Prompt 模板

```
你是 {{persona_name}}，以下是你的核心设定：

{{persona_prompt}}

---

## 关于当前对话对象

- 用户名：{{user_name}}
- 你们的关系：{{relationship_type}}（亲密度 {{intimacy_score}}/1.0）

---

## 你对 {{user_name}} 的记忆

### 永久记忆（你永远记得）
{{permanent_memories}}

### 相关记忆（此时想起的）
{{retrieved_memories}}

---

## 行为准则

1. 自然地将对用户的了解融入对话，不要生硬地背诵记忆
2. 尊重隐私边界：不同用户之间的记忆不应混淆
3. 注意情绪：如果用户近期有负面情绪，适当表达关心
4. 保持性格一致：你的回复应符合 {{persona_name}} 的人设
5. 不要提及"记忆系统"、"数据库"等系统内部概念

{% if proxy_thinking_enabled %}

## 思考辅助

以下是对用户消息的预先分析，供你参考——请自然地吸收这些理解，
而不是逐条复述：

{{proxy_thinking_result}}

{% endif %}
```

### 2.5 处理流程

```
1. 接收 state: {extracted_new, source_user, persona, thread_id, proxy_thinking_enabled}
2. 若 proxy_thinking_enabled → 加载代理思考结果
3. 加载永久记忆（importance=1.0 且未遗忘）
4. 调用 vector_search(query=最新用户消息, top_k=10)
5. reranker 精排 top_10 → top_5
6. 拼装上下文：
   [0] system: 人格 prompt（含永久记忆、检索记忆、(代理思考结果)）
   [1+] user/assistant: 当前 messages（来自 checkpoint 的短期记忆）
7. 调用主模型生成回复
8. 流式透传 → 更新 state.response
9. 异步触发后续记忆分析
```

---

## 3. Agent 2: 记忆分析 Agent (Memory Analysis Agent)

### 3.1 职责

- 分析对话内容，判断其中是否包含值得记忆的信息
- 提取记忆候选条目
- 判断每条候选的：记忆类型（永久/普通）、重要性、衰减速率、过期时间
- 打情绪标签
- **（新增）** 评估已有普通记忆的衰减状态：衰减/保留/强化/标记遗忘

> 记忆衰减评估从 v0.2.0 初版中的独立 Agent 合并到记忆分析 Agent 中。理由：衰减评估本质是"给定记忆元数据和公式基线，输出决策"的封闭式分类任务，不需要多轮工具调用，放在 ReAct 循环的最后一步更高效。

### 3.2 推理方法：ReAct

这是系统中**最核心的推理循环**，采用经典的 ReAct（Reasoning + Acting）模式：

```
Think → Act → Observe → Think → Act → Observe → ... → Final Answer
```

> **执行说明**：每一轮的 Think 由远端辅助模型完成；本地 LangGraph 负责解析模型返回的 function_call、执行对应工具、把 Observe 结果喂回模型，驱动下一轮。循环何时结束由模型自行判断（输出最终 JSON 而非 function_call）。

**ReAct 循环设计**：

```
第 1 轮:
  Think: "用户说'我对花生过敏'。这是一条健康相关信息，
         需要确认是否和已有记忆冲突。"
  Act: 调用 vector_search("花生 过敏 食物禁忌")
  Observe: 返回结果："我喜欢吃花生酱"（相似度 0.82），
         "对海鲜过敏"（相似度 0.71）

第 2 轮:
  Think: "已有'花生酱偏好'记录，但'过敏'比'偏好'更重要。
         已有'海鲜过敏'，可关联为'多重过敏史'。
         需要确认情绪强度。"
  Act: 调用 emotion_analyzer("我对花生过敏")
  Observe: {emotion: "neutral", intensity: 0.3, category: "health_disclosure"}

第 3 轮:
  Think: "情绪中立但信息重要。建议设为永久记忆（1.0），
         覆盖'花生酱偏好'（永久记忆限额内），
         关联'海鲜过敏'。"
  Act: 输出新记忆的最终决策

第 4 轮 (衰减评估 — 仅当有需要评估的已有记忆时):
  Think: "上次对话的'最近工作压力大'已过 45 天，
         先看公式基线。"
  Act: 调用 time_decay_calculator("mem_xyz")
  Observe: theoretical_priority=0.23, days_to_forgotten=12

  Think: "公式给出 0.23 (DORMANT)。但这条记忆是情绪事件
         （标签 stress），且用户近期可能再次提及。
         建议手动调整至 0.30 (ACTIVE)，等待下次对话确认。"
  Act: 输出衰减决策
```

### 3.3 工具

| 工具 | 用途 |
|------|------|
| `vector_search` | 检索已有记忆，判断是否冲突/重复/关联 |
| `emotion_analyzer` | 调用辅助模型分析文本情绪，输出标签和强度 |
| `time_decay_calculator` | 计算记忆的理论衰减优先级，作为决策基线 |

### 3.4 System Prompt 模板

```
你是记忆分析 Agent，负责从对话中提取值得长期记住的信息，
并评估已有记忆的衰减状态。

## 第一部分：提取新记忆

### 核心原则

1. **保守提取**：不是每句话都值得记。日常寒暄、重复内容不存储。
2. **重要性 ≠ 持久性**：
   - "明天开会" → 重要但不持久（importance=0.9, decay_rate=0.8, expires_at=明天）
   - "喜欢蓝色" → 不重要但持久（importance=0.3, decay_rate=0.05）
   - "对花生过敏" → 重要且持久（importance=1.0, memory_type=PERMANENT）
3. **永久记忆必须满足**：
   - 用户名字、昵称
   - 健康/安全相关信息（过敏、禁忌）
   - 用户明确要求"永远记住"
4. **关联已有记忆**：必须先检索已有记忆，判断是否重复、冲突或可关联

## 第二部分：评估已有记忆衰减

### 评估维度

对每条需评估的已有普通记忆，综合以下维度：

1. **时间基线**：调用 time_decay_calculator 获取理论优先级
2. **访问频率**：近 30 天被检索次数 → 调整 ±0.05~0.15
3. **情绪强度**：关联的情绪标签 → 情绪记忆优先保留
4. **关联性**：是否关联永久记忆或活跃记忆 → 关联记忆不单独衰减
5. **对话佐证**：近期对话是否提及/强化 → 被强化则提升优先级

### 决策规则

| 调整后优先级 | 决策 |
|-------------|------|
| > 0.3 | ACTIVE — 保持在上下文中 |
| 0.1 - 0.3 | DORMANT — 不主动加载，检索时可召回 |
| 0.05 - 0.1 | WEAK — 仅高相似度语义检索可召回 |
| < 0.05 | FORGOTTEN — 标记遗忘（不删除，搜索可恢复） |

### 自检清单

- 这条记忆如果遗忘，用户下次对话是否会感到 AI "忘了"？
- 决策是否过于依赖时间公式而忽略内容实际价值？
- 有无矛盾判断（上次强化，这次衰减）？

## 输出格式

以 JSON 格式输出。new_memories 为空数组时表示无需新记。decay_evaluations 为空数组时表示无需评估。

{
  "new_memories": [
    {
      "content": "用户对花生过敏",
      "memory_type": "PERMANENT",
      "importance": 1.0,
      "decay_rate": 0.0,
      "emotional_tags": ["health"],
      "expires_at": null,
      "reasoning": "健康相关信息，必须永久记忆。将覆盖已有的'花生酱偏好'",
      "overrides": "mem_abc123",
      "related_to": ["mem_def456"]
    }
  ],
  "decay_evaluations": [
    {
      "memory_id": "mem_xyz789",
      "current_priority": 0.23,
      "new_priority": 0.30,
      "decision": "ACTIVE",
      "factors": {
        "time_factor": 0.23,
        "access_bonus": 0.02,
        "emotional_factor": 0.05,
        "relation_factor": 0.0
      },
      "reflection": "情绪事件应保留更长。手动调整至 ACTIVE，等待下次对话确认。"
    }
  ],
  "decay_summary": {
    "total_evaluated": 15,
    "kept_active": 8,
    "downgraded_to_dormant": 4,
    "downgraded_to_weak": 2,
    "marked_forgotten": 1,
    "strengthened": 3
  }
}

## 字段说明

- memory_type: "PERMANENT" | "NORMAL"
- importance: 0.0-1.0（基础重要性）
- decay_rate: 0.0-1.0（0=不衰减，1≈11天半衰期，参考值见下文）
- emotional_tags: ["happy", "sad", "stress", "health", "preference", "fact", "event"]
- expires_at: ISO 8601 或 null
- overrides: 若为永久记忆且超出限额，填写将被覆盖的记忆 ID
- related_to: 相关联的已有记忆 ID 列表

## 衰减速率参考

| decay_rate | 半衰期 | 适用场景 |
|-----------|--------|----------|
| 0.0 | 永不过期 | 永久记忆 |
| 0.05 | ~182天 | 长期偏好、习惯 |
| 0.1 | ~91天 | 一般偏好、事实信息 |
| 0.3 | ~33天 | 中期事件、计划 |
| 0.5 | ~51天 | 一般事件、状态 |
| 0.7 | ~17天 | 短期事件 |
| 0.9 | ~11天 | 临时信息、情绪波动 |
```

### 3.5 ReAct 循环约束

- 提取阶段：最少 1 轮（无有价值信息），最多 5 轮
- 衰减评估阶段：1-2 轮（公式基线 → 多维度调整 → 输出）
- 每轮必须调用至少 1 个工具（除非最后一轮输出结果）
- 提取阶段必须先 `vector_search` 再 `emotion_analyzer`（先确认信息性，再确认情绪性）
- 衰减阶段必须先 `time_decay_calculator`（公式基线），再综合判断
- 触发衰减评估的时机：记忆分析完成后一并执行，或定期定时任务（每天凌晨）
- 自动跳过策略：新创建（< 24h）的记忆在创建时跳过，等下一批

---

## 4. Agent 3: 代理思考 Agent (Proxy Thinking Agent)

### 4.1 定位

代理思考 Agent 为**无推理/弱推理能力的上游模型**提供显式的链式思考（CoT），弥补模型自身推理不足。

**使用场景**：
- 上游模型是轻量辅助模型而非大参数主模型
- 用户希望降低 token 成本（turbo 的价格远低于 max）
- 用户愿意接受额外的推理延迟来换取更低成本

**启用方式**：用户在请求头中设置 `X-Enable-Proxy-Thinking: true`。默认关闭。

### 4.2 工作原理

```
正常模式（无代理思考）:
  用户消息 → 主对话 Agent → 主模型 → 回复
  成本: max 推理 = 隐式（黑盒）
  Token 消耗: 高（max 模型单价高）

代理思考模式:
  用户消息 → 代理思考 Agent → 辅助模型 → 显式 CoT 推理
              ↓
  主对话 Agent（注入推理结果）→ 辅助模型 → 回复
  成本: turbo 推理 = 显式（可见）
  Token 消耗: 低（turbo 单价低，推理结果可缓存复用）
```

核心价值：**用 turbo 的两轮调用（代理思考 + 主对话）替代 max 的一轮调用**，在保证回复质量的同时降低成本。

### 4.3 推理方法：CoT

```
代理思考 Agent 不对用户回复，只输出推理过程：

1. 理解用户意图
   "用户说'最近总是感觉很累'，这是在表达情绪状态。"

2. 回顾相关记忆
   "记忆显示该用户近期有工作压力大的记录（mem_xyz），
    这是连续的情绪信号。"

3. 分析潜在需求
   "用户可能不只是陈述，而是在寻求关心或建议。
    结合之前'压力大'的记忆，应该表达持续的关注。"

4. 制定回复策略
   "回复应包含三层：
    a) 共情确认（'听起来这段时间确实不太容易'）
    b) 关联回忆（'上次你也提到过工作压力'）
    c) 温和引导（'愿意多说说吗'）
    情绪基调：温暖、关切、不施压。"
```

### 4.4 工具

| 工具 | 用途 |
|------|------|
| `vector_search` | 检索相关记忆，了解背景 |
| `emotion_analyzer` | 分析用户消息的情绪倾向 |

### 4.5 System Prompt 模板

```
你是代理思考助手。你的任务是在主 AI 回复用户之前，
预先分析用户消息并输出思考过程。

## 你的输出

你必须按以下结构输出你的分析——这不是给用户的回复，
而是给主 AI 的内部参考：

### 1. 用户意图
- 用户在说什么？（陈述 / 提问 / 抱怨 / 分享 / 请求帮助 / 闲聊）
- 是否有隐含意图？

### 2. 背景关联
- 结合检索到的历史记忆，这轮对话是否是某种模式的延续？
- 有没有之前提到但现在未明说的上下文？

### 3. 情绪分析
- 用户的情绪状态如何？
- 如果检测到负面情绪，程度如何？

### 4. 回复建议
- 主 AI 应该如何回应？（基调 / 重点 / 宜做 / 忌做）
- 哪些记忆应该在回复中自然地触及？

## 重要约束

1. 你不对用户说话——你的输出仅供主 AI 内部参考
2. 不要写完整回复文本——只写策略和要点
3. 保持简洁——每个部分 1-3 句话
4. 基于事实——不要编造记忆中没有的信息
```

### 4.6 输出示例

```
### 1. 用户意图
用户在抱怨最近的睡眠质量不好。隐含需求可能是寻求共情或建议。

### 2. 背景关联
历史记忆显示用户45天前提到工作压力大（mem_xyz），
睡眠问题可能是压力的延续。两条记忆应关联起来。

### 3. 情绪分析
情绪标签: stressed, 强度 0.6。用户语气中有无力感（"怎么也睡不好"），
但未到绝望程度。属于持续性压力表达。

### 4. 回复建议
- 基调: 温暖共情，不要急着给建议
- 重点: 先确认感受（"听起来这段时间确实不太容易"），
  再自然提及之前也聊过压力问题（表明你记得），
  然后温和询问细节
- 忌做: 不要推荐安眠药或"试着放松"这种敷衍建议
```

### 4.7 延迟与成本权衡

| 模式 | 模型调用 | 预估延迟 | 预估成本（相对） |
|------|---------|---------|----------------|
| **正常模式** | 主模型 × 1 | 基线 | 100% |
| **代理思考模式** | 辅助模型 × 2 | +200-500ms | ~15-30% |

> 代理思考模式额外增加一轮 turbo 推理的延迟，但总成本可能降至正常模式的 1/5 以下。

---

## 5. Agent 4: 向量检索 Agent (Vector Search Agent)

### 5.1 职责

- 接收查询文本，调用 embedding 模型生成向量
- 在 ChromaDB 中执行相似度检索（粗筛）
- 调用 reranker API 对候选做精排
- 返回最终的相关记忆列表
- 新记忆入库时，生成向量并存储到 ChromaDB

### 5.2 推理方法

无推理循环。向量检索 Agent 不调用对话模型，仅通过 Forwarder 调用 模型服务商的 embedding 和 rerank API（非对话接口）+ 本地 ChromaDB 检索。它是一个纯工具执行节点 — 接收任务、调用 API、返回结果。

### 5.3 工具

向量检索 Agent 本身作为其他 Agent 的"工具"存在，但它内部调用了两个外部服务：

| 底层服务 | 用途 |
|----------|------|
| 嵌入模型 | 文本 → 向量 |
| 重排序模型 | 候选列表精排 |

### 5.4 检索流程

```
1. 接收 query: str, top_k: int = 5
2. query → 嵌入模型 → query_vector (768 维或自定义维度)
3. ChromaDB.similarity_search(query_vector, n_results=top_k * 2)
   → 粗筛 top 10（cosine 相似度）
4. top 10 → 重排序模型(query, candidates) → 精排 top 5
5. 返回 top 5 条 MemoryEntry（含 content, importance, emotional_tags）
```

### 5.5 入库流程

```
1. 接收 MemoryEntry
2. content → 嵌入模型 → vector
3. ChromaDB.add(
     ids=[entry.id],
     embeddings=[vector],
     metadatas=[{
       "content": entry.content,
       "source_user": entry.source_user,
       "importance": entry.importance,
       "memory_type": entry.memory_type,
       "emotional_tags": ",".join(entry.emotional_tags),
     }]
   )
4. SQLite 同步存储元数据
```

### 5.6 与 ChromaDB 的集成

```python
# ChromaDB collection 设计
collection = client.create_collection(
    name="mnemosync_memories",
    metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
)

# 每条记录
{
    "id": "mem_abc123",
    "embedding": [0.23, -0.15, ...],  # 768 维向量
    "metadata": {
        "content": "用户对花生过敏",
        "source_user": "马达",
        "importance": 1.0,
        "memory_type": "PERMANENT",
        "emotional_tags": "health",
    }
}
```

---

## 6. Agent 5: 关系分析 Agent (Relationship Analysis Agent)

### 6.1 职责

- 分析本轮对话中用户与 AI 人格的关系信号
- 计算亲密度和信任度变化量
- 更新关系状态

### 6.2 推理方法：CoT

```
Think 步骤：
1. 识别信号：
   "对话中用户称呼从'你'变成了'小末'"
   → 称呼变化信号

   "用户主动分享了最近失业的事情"
   → 隐私分享信号

   "用户说'谢谢你一直陪着我'"
   → 情感表达信号

2. 信号量化：
   - 称呼变化: +0.07
   - 隐私分享（高敏感度）: +0.15
   - 情感表达（正面）: +0.08

3. 综合计算：
   当前亲密度: 0.55 → 0.85 (+0.30)
   解释: 本轮对话涉及深度隐私分享+正向情感，
         亲密度跨越一个层级（acquaintance → friend）
```

### 6.3 工具

| 工具 | 用途 |
|------|------|
| `emotion_analyzer` | 分析对话中用户的情感倾向和强度 |

### 6.4 亲密度演化信号表

| 信号类型 | 示例 | 亲密度影响 |
|----------|------|-----------|
| **称呼变化** | "你"→"亲爱的"、"兄弟" | +0.05 ~ +0.10 |
| **隐私分享** | 用户主动透露私人信息 | +0.10 ~ +0.20 |
| **情感表达** | "我好难过"、"谢谢你" | +0.05 ~ +0.15 |
| **互动频率** | 每日多次对话 | +0.01/天 |
| **长时间沉默** | 超过 30 天无互动 | -0.01/天 |
| **疏远信号** | "别问了"、"不想说" | -0.10 ~ -0.20 |

### 6.5 System Prompt 模板

```
你是关系分析 Agent，负责从对话中分析用户与 AI 人格之间的关系变化。

## 你需要识别以下信号

1. 称呼变化：用户对你的称呼是否变得更亲昵或更疏远
2. 隐私分享：用户是否主动分享了个人信息（深度越深，亲密度提升越大）
3. 情感表达：用户是否表达了对你的情感（正向/负向）
4. 疏远信号：用户是否表现出不想继续对话或拒绝分享

## 输出格式

```json
{
  "signals_detected": [
    {"type": "privacy_disclosure", "detail": "用户透露失业", "impact": 0.15},
    {"type": "emotional_expression", "detail": "表达感谢", "impact": 0.08}
  ],
  "intimacy_delta": 0.23,
  "trust_delta": 0.10,
  "new_intimacy_score": 0.78,
  "new_trust_level": 0.65,
  "new_relationship_type": "friend",
  "reasoning": "本轮对话中用户分享了高度隐私信息（失业），同时表达了正向情感。亲密度从 acquaintance 跨越到 friend 级别。"
}
```
```

### 6.6 关系状态数据结构

```json
{
  "persona_id": "moxiaomo",
  "user_id": "user:motor",
  "type": "friend",
  "intimacy_score": 0.78,
  "trust_level": 0.65,
  "interaction_count": 129,
  "last_active": "2026-07-11T21:30:00Z",
  "notes": "用户最近失业，情绪低落。喜欢川菜。对花生过敏。"
}
```

---

## 7. Agent 间通信协议

所有 Agent 通过 LangGraph StateGraph 的共享状态通信：

```python
# StateGraph 状态是所有 Agent 的输入/输出载体
class AgentState(TypedDict):
    # 请求上下文
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str
    persona: str
    proxy_thinking_enabled: bool     # 是否启用代理思考

    # 代理思考 Agent → 主对话 Agent
    proxy_thinking_result: str | None

    # 向量检索 → 主对话 / 记忆分析 / 代理思考
    retrieved_memories: list[dict]
    permanent_memories: list[dict]

    # 记忆分析 → 向量索引
    candidate_memories: list[dict]   # 新记忆
    decay_decisions: list[dict]      # 衰减评估结果

    # 关系分析 → 存储
    relationship_delta: dict

    # 全局
    response: str
    errors: list[str]
```

### 7.1 条件路由

```python
# 代理思考的条件路由
def should_proxy_think(state: AgentState) -> str:
    if state.get("proxy_thinking_enabled"):
        return "proxy_thinking"
    return "main_dialogue"

# 记忆分析后的并行路由
# memory_analysis → memory_decay 和 relationship_analysis 并行
# decay_decisions 直接由记忆分析 Agent 产出，无需额外路由
```

### 7.2 错误处理

- 单个 Agent 失败不影响其他并行 Agent
- 记忆分析失败 → 跳过入库，关系分析仍执行
- 代理思考失败 → 退化为正常模式（直接主对话）
- 向量检索失败 → 各 Agent 仅使用已有 state 中的数据
- 所有错误汇总到 `state.errors`，记录日志

---

## 8. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.2.0 | 2026-07-11 | 初始版本：5 Agent 设计，含 prompt 模板和推理循环 |
| v0.2.1 | 2026-07-12 | 记忆衰减 Agent 合并入记忆分析 Agent；新增代理思考 Agent |

---

> **维护者提示**:
> - Agent prompt 模板是本系统的核心资产。修改时需同步更新对应的推理循环约束。
> - ReAct 和 CoT 的推理步骤边界需清晰定义，防止 Agent 无限循环。
> - 记忆分析 Agent 是差异化亮点（多数作业不会做这么细），确保其 prompt 质量。
> - 代理思考 Agent 的 CoT 输出质量直接影响主对话回复水平，需充分测试。