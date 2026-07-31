# 记忆系统设计 | Memory System Design

> **文档版本**: v0.3.4
> **创建时间**: 2026-03-29
> **最后更新**: 2026-08-01
> **状态**: 与代码同步

**结构**: 短期记忆 (v0.2.6, 跨前端对话流水双窗装填) + 长期记忆 (向量库 + SQLite + 衰减模型)。两者独立存储、独立生命周期, 在装填时汇合成同一份主对话 messages。

---

## 1. 概述 (Overview)

Mnemosync 的记忆系统是整个项目的核心，负责实现**跨平台人格记忆同步**。

### 1.1 核心价值

```
┌─────────────────────────────────────────────────────────────┐
│  Mnemosync 记忆同步原理                                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  前端 A (AstrBot)     前端 B (AIRI)     前端 C (Web)        │
│  "我叫马达"           "我压力大"        "你喜欢什么"        │
│       ↓                  ↓                  ↓               │
│       └──────────────────┼──────────────────┘               │
│                          ↓                                   │
│              ┌───────────────────────┐                      │
│              │   Mnemosync 记忆池     │                      │
│              │  ┌─────────────────┐  │                      │
│              │  │ 马达的完整记忆   │  │ ← 统一的人格记忆     │
│              │  │ - 叫马达        │  │    不是分散的会话日志 │
│              │  │ - 压力大        │  │                      │
│              │  │ - 喜欢...       │  │                      │
│              │  └─────────────────┘  │                      │
│              └───────────────────────┘                      │
│                          ↓                                   │
│              不管哪个前端来对话                              │
│              都记得"马达压力大"                              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **记忆不是为了隔离数据** | 而是为了在合适的语境下，唤起合适的记忆 |
| **重要性与持久性分离** | 重要的事不一定永久，永久的事不一定重要 |
| **衰减不等于遗忘** | 衰减是优先级降低，遗忘是彻底不主动想起 |
| **永久记忆需要限额** | 避免上下文混乱，保证核心信息不丢失 |
| **Agent 驱动决策** | 记忆分析、衰减评估由 Agent 智能执行，非固定公式（v0.2 新增） |

### 1.3 v0.1.0 → v0.2.6 核心变化

| 维度 | v0.1.0（确定性管道） | v0.2.0（Agent 驱动） | v0.2.6 (跨前端整合) |
|------|---------------------|---------------------|---------------------|
| **记忆提取** | 消息提取 + 哈希去重 | 记忆分析 Agent (ReAct) | 后台图路径不变 |
| **记忆分类** | 固定规则 | Agent 语义判断 | 不变 |
| **衰减计算** | 固定公式 | Agent CoT 多维 + 公式兜底 | 不变 |
| **记忆检索** | SQL LIKE | embedding + reranker | 不变 (v0.2.4 嵌入模型单绑定 + Reindex) |
| **短期记忆** | 无 | LangGraph checkpoint (thread_id 分区) | **服务端 `conversation_turns` 流水 + 双窗装填, 忽略客户端历史** |

---

## 1.4 短期记忆 (v0.2.6) — 跨前端对话流水

Mnemosync 的核心承诺是 **"多个前端 = 同一个用户 = 同一份记忆"**。v0.2.5 之前, 每次 `/v1/chat/completions` 用的都是**客户端传来**的 `messages`; 换前端立刻断, 客户端清空对话服务器也一并失忆。v0.2.6 把短期记忆的真相源从"客户端历史"迁到"服务端 append-only 流水":

### 存储

单表 `conversation_turns` (库: `data/conversation.db`, 独立于 memory.db 避免 WAL 争用):

```sql
CREATE TABLE conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,             -- user | assistant
    content TEXT NOT NULL,
    ts TIMESTAMP NOT NULL,
    token_count INTEGER NOT NULL,   -- 估算 (len//2 + 8)
    source_frontend TEXT,           -- 派生自 api_key.note, 仅观测
    actor_id TEXT,                  -- v0.3.0: 参与者 Actor ID
    space_id TEXT,                  -- v0.3.0: 会话空间 ID (群聊分区)
    external_event_id TEXT,         -- v0.3.0: 平台侧事件 ID (幂等)
    committed_sequence INTEGER,     -- v0.3.0: 空间内单调递增序号 (MAX+1, 同事务分配)
    late_arrival INTEGER NOT NULL DEFAULT 0  -- v0.3.0: 事件时间早于空间最新已提交
);
CREATE INDEX idx_conversation_turns_ts ON conversation_turns(ts DESC);
CREATE INDEX idx_conv_space_seq ON conversation_turns(space_id, committed_sequence);
```

**空间事件流 (v0.3.0)**: 群聊/多用户场景按 `space_id` 分区 — 每个空间内轮次在提交时分配 `committed_sequence` (空间内 MAX+1, 同事务分配), 事件时间早于空间内最新已提交时间时标记 `late_arrival`。无 `space_id` 的私聊/非归属轮次不分配序号, 仍按 `ts` 定序。装填上下文时 `space_id` 非空只读本空间流水 (`list_for_space`), 避免其他空间对话泄入。

### 写入 (forward.py)

流式与非流式路径都在主对话完成后写两条:

```python
# _handle_stream 尾部
await conversation_store.append("user", new_user_content,
                                 token_count=token_count_for_storage(new_user_content),
                                 source_frontend=source_frontend,
                                 actor_id=actor_id,           # v0.3.0
                                 space_id=space_id,           # v0.3.0
                                 external_event_id=ext_id)    # v0.3.0
await conversation_store.append("assistant", collected_response_text,
                                 token_count=token_count_for_storage(collected_response_text),
                                 source_frontend=source_frontend,
                                 space_id=space_id)           # v0.3.0
```

- `new_user_content` = 客户端 messages 里**最后一条 user** 的文本 (前面的历史全部忽略)
- `source_frontend` = `api_key.note` (服务器派生, 不信任客户端 header)
- `actor_id` / `space_id` = 由身份解析器按策略解析 (v0.3.0), 详见 [identity.md](identity.md)

### 装填 (build_short_term_history)

```python
BuiltContext = await build_short_term_history(
    store=conversation_store,
    now=now,
    window_days=settings.storage.short_term_days,  # 7
    context_length=main_candidate.context_length,   # 从 role_bindings 元数据
    system_text=rendered_system,                    # 已 render 完的 system
    new_user_text=new_user_content,
    max_tokens_hint=request.max_tokens,
    space_id=space_id,                              # v0.3.0: 非空时只读本空间
)
```

**双窗**:
1. **时间窗** (硬边界): 只取 `ts >= now - short_term_days`, 老于此的不进候选
2. **模型窗** (软预算): `budget = ctx - est(system) - est(new_user) - reserve_output`
   - `reserve_output` = 客户端 `max_tokens`; 缺省 `min(4096, ctx/4)` 下限 512
   - 从最老那端往新累加 token, 累计不超预算 → 保留末端 (最近) 的对话

### Token 估算

`estimate_tokens(text) = len(text) // 2 + 8`。混合中英启发式 + 8 tokens 结构 overhead。不接入真实 tokenizer:
- 换模型时 tokenizer 形变太大, 中间件维护成本高
- 有保留区兜底, 估算偏差不会触发 4001
- `token_count` 落库口径与装填时估算完全一致

### 清理

`lifespan` 启动时起后台任务 (`_conversation_prune_loop`):
- 启动即跑一次 `delete_before(now - short_term_days)`
- 之后每 24h 跑一次
- 应用 shutdown 时被 cancel

### 面板重置

- `GET /panel/admin/conversation-turns?limit=N` — 按 ts 降序列出最近 N 条 (面板"短期记忆"页面用)
- `DELETE /panel/admin/conversation-turns` — 全清
- `DELETE /panel/admin/conversation-turns?since=<iso>` — 只清早于 cutoff 的

**注意**: 客户端 UI 的"清空对话"按钮只影响客户端自己的显示状态, 不会调这两个端点; 服务器的连续记忆只有面板 (或直接调 admin API) 才能真正抹掉。这是设计, 见 [dev-decisions.md 跨前端短期记忆](../dev-decisions.md)。

### 与长期记忆的分工

- **短期记忆**: 上下文连续性; 逐字逐句; 会被时间窗淘汰
- **长期记忆**: 事实性记忆点 (人格核心 / 偏好 / 事件); 被记忆分析 Agent 抽取; 走衰减模型; 靠语义检索召回

同一句话可能同时进短期流水 (原文) 与长期记忆 (抽取后的关键事实), 各自独立生命周期。

---

## 2. 记忆分类 (Memory Classification)

### 2.1 双类型系统

```
记忆系统
├── 永久记忆 (PERMANENT)
│   ├── 核心记忆 (importance=1.0) — 不可覆盖
│   └── 偏好记忆 (importance=0.5) — 可覆盖
│
└── 普通记忆 (NORMAL)
    ├── 重要性 (importance: 0.0-1.0)
    ├── 衰减速率 (decay_rate: 0.0-1.0)
    └── 过期时间 (expires_at: optional)
```

**核心洞察**：

- **重要性 ≠ 持久性**
  - "明天开会"：重要但短期
  - "喜欢蓝色"：不重要但长期
- **衰减 ≠ 遗忘**
  - 衰减：优先级降低，不主动想起
  - 遗忘：标记为 FORGOTTEN，搜索才能恢复

---

### 2.2 永久记忆 (Permanent Memory)

**定义**：不衰减的记忆，除非被覆盖或删除。

| 类型 | 重要性 | 可覆盖 | 例子 |
|------|--------|--------|------|
| **核心记忆** | 1.0 | ❌ 否 | 用户名字、过敏信息 |
| **偏好记忆** | 0.5 | ✅ 是 | 喜好、习惯、偏好 |

**限额机制**：
- 永久记忆上限：**15 条**
- 超出时：记忆分析 Agent 决定覆盖哪条偏好记忆
- 核心记忆永不被覆盖

**Agent 判断策略**（由记忆分析 Agent ReAct 执行）：

```
✅ 存储为永久记忆:
- 用户名字、昵称
- 过敏、禁忌（健康相关）
- 用户明确说"永远记住"
- Agent 判断为对人格认知至关重要的信息

❌ 不存储为永久记忆:
- 一般偏好 → 普通记忆
- 临时信息 → 普通记忆
- 日常闲聊 → 不存储或短期普通记忆
```

---

### 2.3 普通记忆 (Normal Memory)

**定义**：遵循衰减模型的记忆，优先级随时间降低。

**核心参数**：

| 参数 | 类型 | 说明 | 设定者 |
|------|------|------|--------|
| `importance` | 0.0-1.0 | 基础重要性 | 记忆分析 Agent |
| `decay_rate` | 0.0-1.0 | 衰减速率（0=不衰减，1=快速衰减） | 记忆分析 Agent |
| `expires_at` | datetime | 过期时间（可选） | 记忆分析 Agent |

**衰减半衰期参考**：

```
decay_rate = 0.05 → 半衰期约 182 天（长期偏好）
decay_rate = 0.1  → 半衰期约 91 天（长期记忆）
decay_rate = 0.3  → 半衰期约 33 天（中期记忆）
decay_rate = 0.5  → 半衰期约 51 天（一般记忆）
decay_rate = 0.7  → 半衰期约 17 天（短期记忆）
decay_rate = 0.9  → 半衰期约 11 天（临时记忆）
```

**过期机制**：
- 过期后重要性降至 1%
- 数据不删除，搜索时可恢复
- 类比：人类"想不起来但看到能记起"

---

## 3. 衰减模型 (Decay Model)

### 3.1 两层衰减机制（v0.2 新增）

v0.2.0 采用**公式兜底 + Agent 覆盖**的双层衰减：

```
第 1 层（公式兜底）：按固定公式计算理论优先级
                   作为 Agent 的参考基线

第 2 层（Agent 决策）：记忆分析 Agent 执行 CoT + Reflection
                    多维度评估，覆盖或修正公式结果
```

### 3.2 公式兜底（优先级基线）

```
理论优先级 = importance × 衰减因子 × 过期惩罚 + 访问加成

其中:
- 衰减因子 = 0.5^(经过天数 / 半衰期)
- 过期惩罚 = 0.01 (如果过期) 或 1.0 (未过期)
- 访问加成 = log(访问次数 + 1) × 0.05
```

**永久记忆**：优先级恒为 1.0（始终出现在上下文中）

### 3.3 Agent 多维度评估（覆盖公式）

记忆分析 Agent 在公式基线之上，额外评估以下维度：

| 维度 | 说明 | 权重 |
|------|------|------|
| **时间衰减** | 公式计算结果 | 基线 |
| **访问频率** | 近 30 天被检索/调用的次数 | 可 ±0.05~0.15 |
| **情绪强度** | 记忆关联的情绪标签和强度 | 情绪记忆优先保留 |
| **关联性** | 是否与永久记忆或其他活跃记忆关联 | 关联记忆不单独衰减 |
| **对话佐证** | 近期对话中是否再次提及/强化 | 被强化 → 提升优先级 |

> 详细的衰减评估流程见 [Agent 设计文档 §3](agents.md#3-agent-2-记忆分析-agent-memory-analysis-agent)。

### 3.4 遗忘阈值

```
优先级 > 0.3  → ACTIVE   — 出现在主对话上下文中
优先级 0.1-0.3 → DORMANT  — 不主动加载，语义检索可召回
优先级 0.05-0.1 → WEAK    — 仅高相似度语义检索可召回
优先级 ≤ 0.05 → FORGOTTEN — 标记遗忘（不删除，搜索仍可恢复）
```

**生物学类比**：
- ACTIVE = 工作记忆（随时能想起）
- DORMANT = 长期记忆（需要提示才能想起）
- FORGOTTEN = 遗忘但可恢复的记忆

### 3.5 访问加成

经常被访问的记忆衰减更慢：

```
访问次数    访问加成
0          +0.00
1          +0.03
5          +0.09
20         +0.16
```

记忆分析 Agent 可在此基础上根据访问的**质量**（是主对话主动调用还是检索巧合命中）进行调整。

---

## 4. 记忆检索 (Memory Retrieval) — v0.2 新增

### 4.1 从哈希匹配到语义检索

```
v0.1.0: content_hash == stored_hash → 精确匹配
v0.2.0: embed(query) → cosine_similarity → reranker → 语义匹配
```

### 4.2 检索流程

```
查询文本（最新用户消息）
    │
    ▼
嵌入模型 → query_vector
    │
    ▼
ChromaDB.query(query_vector, where=受众粗筛子句, n_results=top_k * 3)   ← v0.3.0
    │  余弦相似度粗筛 (where 为 $or: 自己桶 / PUBLIC / 本空间)
    ▼
candidate_list
    │
    ▼
AudienceFilter.is_visible(entry, retrieval_ctx)                        ← v0.3.0 精筛
    │
    ▼
重排序模型(query, candidates) → 精排
    │  深度语义相关性打分
    ▼
final_results (top 5)
    │
    ▼
返回给主对话 Agent → 拼入上下文
```

### 4.3 为什么需要 reranker？

嵌入模型（embedding）为速度优化，语义理解不够精细。Reranker 对每条候选做逐字对比，能区分细微差异：

```
查询: "我对花生过敏"

embedding 粗筛结果:
  "我喜欢吃花生酱" (cosine 0.82)  ← 相似但不相关
  "我对海鲜过敏" (cosine 0.71)    ← 真正相关

reranker 精排后:
  "我对海鲜过敏" (relevance 0.94) ← 排第一
  "我喜欢吃花生酱" (relevance 0.31) ← 排末尾
```

### 4.4 受众过滤 (v0.3.0)

多用户场景下, 检索**先按受众过滤再交给模型** — 不靠 prompt 防泄露。实现见 [src/core/memory/audience.py](../../src/core/memory/audience.py)。

**两级过滤**:

1. **粗筛** (`AudienceFilter.build_chromadb_where`): ChromaDB `$or` 子句放宽到"可能可见"的超集 — 自己桶 (`source_user`) / PUBLIC (`visibility`) / 本空间 (`space_id`, 仅群聊)。非归属模式仅 `visibility=public`。
2. **精筛** (`AudienceFilter.is_visible`): 取到 `MemoryEntry` 后逐条判定 (关系门槛与策略无法在 ChromaDB 层表达)。候选数因此取 `top_k * 3` 补偿淘汰。

**可见性规则** (`is_visible`):

| 条件 | 可见性 |
|------|--------|
| `visibility = PUBLIC` | 任何上下文可见 (含非归属模式) |
| `entry.source_user == 自己的 effective_user_id` | 可见 (自己桶的记忆, 任何可见性) |
| `entry.space_id == 当前 space_id` 且非 SOURCE_RESTRICTED | 空间成员可见 (群聊共享记忆) |
| `FRIENDS_ONLY` (非来源用户) | 需要 friend / intimate 关系 |
| `CONFIDENTIAL` (非来源用户) | 需要 `trust_level >= 0.7` |
| `custom_policies` 含 `deny:user:<id>` / `deny:actor:<id>` | 一票否决 |
| `custom_policies` 含 `allow:*` 规则 | 构成白名单, 不在名单内不可见 |
| 其余 (SOURCE_RESTRICTED 且非来源用户) | 不可见 |

**群聊关键不变量**: 其他参与者的 SOURCE_RESTRICTED 记忆绝不出现在本用户的上下文中 — 即使同处一个空间。

**永久记忆加载同样受众化**: `list_permanent(source_user, limit, space_id)` 的 SQL 放宽为 自己桶 OR PUBLIC OR (群聊时本空间非 SOURCE_RESTRICTED), 结果再过一遍 `AudienceFilter.filter` (关系门槛只能在 Python 层判)。

身份的来龙去脉 (Actor / UserGroup / effective_user_id / 非归属模式) 见 [identity.md](identity.md)。

---

## 5. 上下文合并 (Context Merging) — v0.2.6

### 5.1 合并策略

发送给上游模型的上下文由 `forward.py` 装填, 不再由主对话 Agent 内部拼装:

```
┌─────────────────────────────────────────┐
│  [0] system: 人格提示词                   │
│              + 永久记忆（最多 permanent_load_top 条）│
│              + 语义检索到的相关记忆         │
│              + 关系状态摘要               │
│              (由 render_main_dialogue_system 生成) │
│  [1..N] user/assistant: 短期对话流水        │
│         (由 build_short_term_history 双窗裁剪)  │
│  [N+1] user: 本轮新消息                    │
│         (来自客户端 messages 最后一条 user)  │
└─────────────────────────────────────────┘
```

**上下文框架可自定义**: 从 v0.2.1 起, system 框架文本 (行为准则、section 标题、记忆容器格式) 从 Python 硬编码迁到 `main_dialogue_frame` 提示词模板 ([defaults/main_dialogue_frame.md](../../src/core/agents/prompts/defaults/main_dialogue_frame.md)), 允许通过 `data/prompts/main_dialogue_frame.md` 覆盖。占位符包含 `__PERSONA_NAME__`, `__PERSONA_SECTION__`, `__CURRENT_SPEAKER__`, `__CHANNEL_TYPE__`, `__SPACE_LABEL__`, `__ACTIVE_PARTICIPANTS__`, `__TRIGGER_REASON__`, `__TOOL_CAPABILITY_HINT__`, `__RELATIONSHIP__`, `__PERMANENT_MEMORIES__`, `__RETRIEVED_MEMORIES__`, `__LOREBOK_ENTRIES__`, `__PROXY_THINKING_SECTION__`。见 [agents.md §7](agents.md#7-自定义-agent-提示词)。

**多说话者上下文** (v0.3.0): `build_short_term_history` 将每条带身份快照的用户消息格式化为 `[显示名 | 前台 external_key]: 内容`; `active_participants` 从裁剪后的短期历史按最近出现顺序去重 (最多 12 人), 并与 `current_speaker` 一起注入主对话 system。内部 actor / group UUID 不进入 Prompt。

**记忆作用域标签**: 受众过滤在装填前完成；通过过滤的记忆还会附加模型可读标签 `[公开]`, `[当前空间共享]`, `[当前发言者私有]` 或 `[受限可见；谨慎使用]`。群聊中的 `SOURCE_RESTRICTED` 记忆额外标记为“群聊中勿主动披露”。标签是模型行为提示, 不能替代 `AudienceFilter` 的确定性过滤。

### 5.2 装填顺序

1. **`render_main_dialogue_system(...)`** → 拼装 system 内容, 返回 str
2. **`build_short_term_history(...)`** → 用双窗算法从 `conversation_turns` 裁剪对话历史 (输入包含已 render 的 system, 用于精确计算 budget)
3. **`build_main_dialogue_messages(system, history, new_user)`** → 组装成 OpenAI messages 列表

### 5.3 上下文配额

模型窗预算 `budget = ctx - est(system) - est(new_user) - reserve_output` 分给 history。永久记忆与语义检索结果占用的是 system 那份 (`render_main_dialogue_system` 内部裁剪); short_term 占的是 history 那份。两者共同受 ctx 上限约束, 但没有严格的百分比配额 — v0.2.6 起改为**先算 system 与 new_user 的实际长度, 剩下的给 history**。

---

## 6. 记忆生命周期 (Memory Lifecycle)

### 6.1 创建流程（Agent 驱动）

```
用户消息 → 主对话 Agent → 生成回复（流式返回）
                │
                ↓（异步，不阻塞）
        记忆分析 Agent (ReAct)
                │
        Think: 这条信息值得记吗？
                │
        Act: vector_search（查重/关联）
                │
        Observe: 已有类似记忆？冲突？
                │
        Think: 判断类型+重要性+衰减率
                │
        Act: emotion_analyzer（情绪标签）
                │
        Observe: 情绪标签+强度
                │
        Think: 综合判断 → PERMANENT 还是 NORMAL？
                │
        输出: 候选记忆 + 参数
                │
                ↓
        检查永久记忆限额
          ├─ 未满 → 直接保存
          └─ 已满 → Agent 决定覆盖哪条偏好记忆
                │
                ↓
        向量检索 Agent: embedding → ChromaDB 入库
                │
                ↓
        SQLite 同步存储元数据
```

### 6.2 衰减流程（Agent 驱动）

```
触发：记忆分析完成后 / 定期任务（每天凌晨）
                │
                ↓
        遍历所有 ACTIVE/DORMANT 普通记忆
                │
                ↓
        记忆分析 Agent (CoT + Reflection)
                │
        对每条记忆:
          1. time_decay_calculator → 理论优先级
          2. 访问频率分析
          3. 情绪强度检查
          4. 关联性分析
          5. 对话佐证检查
          6. 综合决策（衰减/保留/强化/遗忘）
          7. Reflection 自检
                │
                ↓
        批量更新优先级和状态
```

### 6.3 访问流程

```
主对话 Agent 检索记忆时
    │
    ├─ 被选中加载的记忆 → access_count += 1
    │                     last_accessed = now
    │
    └─ 永久记忆始终加载，每次加载也更新 last_accessed
```

---

## 7. 存储架构 (Storage Architecture) — v0.2 新增

### 7.1 双层存储

```
┌─────────────────────────────────────┐
│         ChromaDB（向量层）           │
│  - embedding vector (维度由所选模型决定, │
│    如 DashScope text-embedding-v3 可配 │
│    512/768/1024/1536/2048)            │
│  - collection metadata 锁定             │
│    (service_id, model, dim) (v0.2.4)   │
│  - 关键元数据（content, source_user,   │
│    importance, memory_type, tags）    │
│  - 用途：语义相似度检索（粗筛）       │
└──────────────┬──────────────────────┘
               │ 通过 memory_id 关联
               ▼
┌─────────────────────────────────────┐
│         SQLite（元数据层）           │
│  - 完整 MemoryEntry 字段            │
│  - 关系状态、访问日志、配置          │
│  - 用途：精确查询、CRUD、衰减遍历    │
└─────────────────────────────────────┘
```

### 7.2 MemoryEntry 完整结构

```python
@dataclass
class MemoryEntry:
    id: str                      # 唯一标识
    content: str                 # 记忆文本
    role: str                    # user / assistant
    source_user: str             # 来源用户 (effective_user_id)
    memory_type: MemoryType      # PERMANENT / NORMAL (v0.2 新增)
    importance: float            # 0.0-1.0 (v0.2 新增)
    decay_rate: float            # 0.0-1.0 (v0.2 新增)
    priority: float              # 当前有效优先级 (v0.2 新增)
    access_count: int = 0        # 被访问次数 (v0.2 新增)
    is_forgotten: bool = False   # 是否已遗忘 (v0.2 新增)
    visibility: Visibility       # 可见性
    custom_policies: list[str]   # 自定义策略
    emotional_tags: list[str]    # 情感标签
    related_memories: list[str]  # 关联记忆 ID (v0.2 新增)
    space_id: str | None = None  # 诞生空间 (v0.3.0) — 非空=空间共享记忆候选
    created_at: datetime
    last_accessed: datetime
    expires_at: datetime | None

    @property
    def effective_priority(self) -> float:
        """计算有效优先级（Agent 覆盖前使用）"""
        if self.memory_type == MemoryType.PERMANENT:
            return 1.0
        from math import log
        days = (datetime.now() - self.created_at).days
        half_life = self._decay_rate_to_half_life()
        decay_factor = 0.5 ** (days / half_life) if half_life > 0 else 1.0
        expiration_penalty = 0.01 if self.is_expired() else 1.0
        access_bonus = log(self.access_count + 1) * 0.05
        return self.importance * decay_factor * expiration_penalty + access_bonus
```

### 7.3 SQLite Schema

```sql
-- 记忆条目表（v0.2 新增字段用 ★ 标记）
CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    role TEXT NOT NULL,
    source_user TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'NORMAL',       -- ★ PERMANENT | NORMAL
    importance REAL NOT NULL DEFAULT 0.5,              -- ★ 0.0 ~ 1.0
    decay_rate REAL NOT NULL DEFAULT 0.3,              -- ★ 0.0 ~ 1.0
    priority REAL NOT NULL DEFAULT 0.5,                -- ★ 当前有效优先级
    access_count INTEGER NOT NULL DEFAULT 0,           -- ★ 访问次数
    is_forgotten INTEGER NOT NULL DEFAULT 0,           -- ★ 0 = 活跃, 1 = 遗忘
    visibility TEXT NOT NULL DEFAULT 'source_restricted',
    custom_policies TEXT,
    emotional_tags TEXT,
    related_memories TEXT,                             -- ★ JSON 数组
    space_id TEXT,                                     -- ★ v0.3.0: 诞生空间
    created_at TIMESTAMP NOT NULL,
    last_accessed TIMESTAMP,
    expires_at TIMESTAMP
);

-- 索引
CREATE INDEX idx_source_user ON memory_entries(source_user);
CREATE INDEX idx_memory_type ON memory_entries(memory_type);
CREATE INDEX idx_priority ON memory_entries(priority DESC);
CREATE INDEX idx_is_forgotten ON memory_entries(is_forgotten);
CREATE INDEX idx_created_at ON memory_entries(created_at DESC);
CREATE INDEX idx_mem_space ON memory_entries(space_id);  -- v0.3.0
```

**关系表 (v0.2.10 起 3 个 nullable 列; NULL = 沿用 TOML 基线; v0.3.0 user_id 列存 effective_user_id)**

```sql
CREATE TABLE relationships (
    persona_id           TEXT NOT NULL,
    user_id              TEXT NOT NULL,                  -- v0.3.0: effective_user_id (记忆与关系隔离边界)
    intimacy             REAL NOT NULL DEFAULT 0.0,   -- 0.0 ~ 1.0
    trust                REAL NOT NULL DEFAULT 0.0,
    stage                TEXT NOT NULL DEFAULT 'stranger',
    memory_count         INTEGER NOT NULL DEFAULT 0,
    last_interaction     TIMESTAMP,
    created_at           TIMESTAMP NOT NULL,
    persona_addressing   TEXT,                        -- v0.2.10 nullable
    user_addressing      TEXT,                        -- v0.2.10 nullable
    context              TEXT,                        -- v0.2.10 nullable
    PRIMARY KEY (persona_id, user_id)
);

-- v0.2.10: 字段级审计, 一次多字段更新写多行
CREATE TABLE relationship_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    changed_at   TEXT NOT NULL,                       -- ISO 8601
    source       TEXT NOT NULL,                       -- 'agent' | 'manual'
    field        TEXT NOT NULL,                       -- persona_addressing | user_addressing | context
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT
);
CREATE INDEX idx_audit_user ON relationship_audit_log(persona_id, user_id, id DESC);
```

**称呼演化 (v0.2.10)**: 关系分析 Agent 通过 [`update_addressing` 工具](tools.md#5-make_update_addressing_toolmemory_store-persona_id-user_id-v0210) 把用户消息中"以后叫我 X"等真诚请求原子写入 `relationships` (3 个 addressing 列) + `relationship_audit_log` (每字段一行 diff)。运行时 `nodes.py._resolve_addressing()` 用**表 → `persona_override.toml` → `config.local.toml [persona.relation]` → 资源默认**四层优先级取值, 面板 `GET /panel/admin/relationship` 直接返回**当前有效值**而非 NULL, `PUT /panel/admin/relationship` 允许人工覆盖并写 source=manual 审计条目, `GET /panel/admin/relationship/audit` 拉最近历史用于回退。

---

## 8. 关键设计决策 (Key Design Decisions)

### 8.1 为什么永久记忆需要限额？

不限额会导致上下文 token 不断膨胀，核心信息被稀释。限额 15 条，由记忆分析 Agent 决定覆盖策略。

### 8.2 为什么重要性与持久性分离？

"明天开会"重要但短期，"喜欢蓝色"不重要但长期。三个独立维度（importance, decay_rate, expires_at）表达更准确。

### 8.3 为什么衰减不等于遗忘？

类比人类：不会真正"删除"记忆，只是优先级降低。数据不删除，语义检索仍可恢复。好处：用户说"我之前说过..."时能找到。

### 8.4 为什么由 Agent 而非固定公式决定衰减？（v0.2 新增）

固定公式只考虑**时间**一个维度。Agent 能同时评估时间、访问频率、情绪强度、关联性和对话佐证 — 更接近人类"选择性记忆"的真实机制。公式作为兜底基线，Agent 做精细化调整。

### 8.5 为什么用 embedding 替代哈希？（v0.2 新增）

哈希只能精确匹配。embedding 能理解：
- "我叫马达" ≈ "我的名字是马达" ≈ "叫我马达就行"
- "压力大" ≈ "最近很焦虑" ≈ "心情不太好"

语义检索让记忆召回更准确，减少遗漏。

---

## 9. 与其他模块的关系 (Relationships)

| 模块 | 关系说明 |
|------|----------|
| **结构化人格** ([persona-definition.md](persona-definition.md)) | `to_legacy_prompt()` 生成 `__PERSONA_SECTION__` 注入主对话 system |
| **forward.py** | 装填上下文: 调 `render_main_dialogue_system` + `build_short_term_history`, 组装成 messages 转发上游; 主对话完成后写 `conversation_turns` (v0.2.6) |
| **主对话 Agent (装填后被上游模型执行)** | 直接消费装填好的 messages, 通过工具调用 `vector_search` 加载额外记忆 (可选) |
| **记忆分析 Agent** | 后台图节点; 从 user turn 抽取事实性记忆, 设定 memory_type/importance/decay_rate; 也评估现有记忆更新 priority/is_forgotten |
| **MemoryRetriever (工具)** | `vector_search.py`; embedding → Chroma 粗筛 → rerank 精排; 供 Agent 或 forward 装填时调用 |
| **关系分析 Agent** | 后台图节点; 更新关系状态, 影响装填时的 system 内容 |
| **短期记忆 (conversation_turns)** | 由 `SqliteConversationStore` 承载, 独立于 memory.db |

---

## 9bis. 维护端点 (Maintenance Endpoints)

三个层级的清理动作, 语义严格分层, 不要混用:

| 端点 | 清什么 | 保留什么 | 何时用 |
|------|--------|---------|-------|
| `POST /panel/admin/memory/prune` | forgotten / expired / `priority < threshold` 的 NORMAL 记忆 | **PERMANENT 全部保留**; 关系 / 短期 / 向量库不动 | 日常瘦身, 只清衰减掉的普通记忆 |
| `POST /panel/admin/memory/reindex` (`prune=true` 可选) | 重建 Chroma collection (换嵌入模型时); prune=true 时顺带按上一列规则清理 | 同上, PERMANENT 一律保留 | 更换嵌入模型 / 修复向量库损坏 |
| `POST /panel/admin/persona/reset` (**v0.2.7**) | **memory_entries 全部** (含 PERMANENT) + **relationships 全部** (亲密度 / 信任度) + **conversation_turns 全部** + Chroma collection | API Key / 服务商 / 模型绑定 / 提示词覆盖 / 管理员 / http_logs / config.local.toml | 想让 Mnemosync 回到"新装"的人格状态 (数据脏了 / 换测试场景 / 想重头开始一段关系) |

### Persona Reset 语义要点

1. **PERMANENT 一并清空** — 这是与 prune 的核心区别。用户明示"重置到新装", 昵称 / 生日 / 关键事实一起清
2. **不主动写回默认 relationship 行** — 下次对话时 `lifecycle.update_relationship` 会自动 `Relationship.create(...)` 补一行 stranger/0/0, 无需服务器多写一次 IO
3. **顺序**: Chroma → memory_entries → relationships → conversation_turns。Chroma 先清保证残余状态里不会有"指向不存在记忆"的向量
4. **每步独立 try/except**: 部分失败不回滚, 结果里 `errors: list[str]` 汇总; 面板会 toast 警告并显示已清计数
5. **与 reindex 互斥**: `progress.state == RUNNING` 时返回 409
6. **`dry_run=True`** 只统计不删, 用于面板"预览 → 确认"两次调用
7. **API Key / 服务商 / 提示词一律不动** — 属于运维配置层, 不是"人格状态"。真想全清就 `rm -rf data/` + `mnemosync init`

面板入口: `/maintenance` 页底部"重置人格状态"卡片; CLI: `persona reset [--dry-run] [--yes]`。

---

## 10. 版本历史 (Version History)

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1.0 | 2026-03-24 | 初始设计（三级记忆模型 + 哈希去重） |
| v0.2.0 | 2026-07-11 | 重构：Agent 驱动决策替代固定公式；双类型系统升级为 Agent 判断；embedding 语义检索替代哈希去重；新增双层衰减（公式兜底 + Agent 覆盖）；ChromaDB + SQLite 双层存储；LangGraph checkpoint 作为短期记忆 |
| v0.2.1 | 2026-07-16 | §5.1 上下文框架文本从硬编码迁到 `main_dialogue_frame.md` 提示词模板, 支持用户覆盖 |
| v0.2.4 | 2026-07-17 | 嵌入角色单绑定 + ChromaDB collection 锁定 `(service_id, model, dim)`; 新增 Reindex + Prune 端点; MemoryEntry 新增 `related_memories` 与 `memory_type` 字段消费路径 |
| v0.2.6 | 2026-07-18 | 短期记忆从 LangGraph checkpoint 迁到服务端 `conversation_turns` 流水; 双时间+模型窗装填; 忽略客户端历史; source_frontend 元数据; 面板可查看/重置 |
| v0.2.7 | 2026-07-18 | 新增 `POST /panel/admin/persona/reset`: 原子清空 memory_entries (含 PERMANENT) / relationships / conversation_turns / Chroma collection, 保留服务商与 API Key。面板 + CLI 二次确认后触发 |
| v0.2.9 | 2026-07-19 | 关系基线抽入 TOML `[persona.relation]` (`persona_addressing` / `user_addressing` / `context`), memory / relationship 两个 Agent prompt 通过占位符消费 |
| v0.2.10 | 2026-07-19 | **关系称呼动态演化**: `relationships` 表加 3 个 nullable 列 (同名字段); 新增 `relationship_audit_log` 表 (字段级 diff, source=agent/manual); 关系分析 Agent 获得 `update_addressing` tool (自证 `reason` ≥ 10 字, `persona_id`/`user_id` 由 factory 闭包绑定不可越权); `nodes.py._resolve_addressing` 优先读表, NULL 回退 TOML 基线; `PUT /panel/admin/relationship` + `GET /panel/admin/relationship/audit` 提供人工 override 与历史回溯 |
| v0.3.0 | 2026-07-26 | **多用户受众化**: `MemoryEntry.space_id` 字段; `source_user` / `relationships.user_id` 语义改为 effective_user_id (不再有 "default" 兜底); 检索两级受众过滤 (§4.4: `$or` 粗筛 + `is_visible` 精筛); `list_permanent` 放宽并过滤; 短期流水按 space_id 分区 (`committed_sequence` / `late_arrival` / `list_for_space`); 幂等缓存保护记忆不被重复写入 (详见 [identity.md](identity.md)) |

---

> **维护者提示**:
> - 记忆系统是 Mnemosync 的灵魂，任何修改需谨慎。
> - 永久记忆限额是保护机制，不要随意提高。
> - 衰减参数需要实际测试调优，Agent 的 CoT 维度权重尤其需要实验验证。
> - embedding 模型的选择影响检索质量，切换模型需重新生成全量向量。"