# 记忆系统设计 | Memory System Design

> **文档版本**: v0.2.0
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-11
> **状态**: 设计中

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

### 1.3 v0.1.0 → v0.2.0 核心变化

| 维度 | v0.1.0（确定性管道） | v0.2.0（Agent 驱动） |
|------|---------------------|---------------------|
| **记忆提取** | 消息提取 + 哈希去重 | 记忆分析 Agent (ReAct) |
| **记忆分类** | 固定规则（名字→永久，偏好→普通） | Agent 语义判断 |
| **衰减计算** | 固定公式 `0.5^(天数/半衰期)` | 衰减 Agent CoT 多维评估 + 公式兜底 |
| **记忆检索** | SQL LIKE / 关键词匹配 | embedding 语义检索 + reranker 精排 |
| **短期记忆** | 无 | LangGraph checkpoint |

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
text-embedding-v3 → query_vector [768 维]
    │
    ▼
ChromaDB.similarity_search(query_vector, n_results=top_k * 2)
    │  余弦相似度粗筛
    ▼
candidate_list (top 10)
    │
    ▼
gte-rerank(query, candidates) → 精排
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

---

## 5. 上下文合并 (Context Merging)

### 5.1 合并策略（v0.2 更新）

发送给上游模型的上下文由主对话 Agent 拼装：

```
┌─────────────────────────────────────────┐
│  [0] system: 人格提示词                   │
│              + 永久记忆（最多 7 条）       │
│              + 检索到的相关记忆（最多 5 条）│
│              + 关系状态摘要               │
│  [1+] user/assistant: 当前对话历史        │
│       （来自 LangGraph checkpoint 短期记忆）│
└─────────────────────────────────────────┘
```

### 5.2 加载优先级

1. **永久记忆** — 始终加载，最多 7 条，按 importance 排序
2. **语义检索记忆** — 以当前消息为 query，embedding → reranker，最多 5 条
3. **短期记忆** — LangGraph checkpoint 中的对话历史（同一 thread_id）

### 5.3 上下文配额

- 永久记忆：最多 50%（保证核心信息）
- 语义检索记忆：最多 30%
- 当前对话：至少 20%（保证对话连贯性）

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
│  - embedding vector (768 维)        │
│  - 关键元数据（content, source_user, │
│    importance, memory_type, tags）   │
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
    source_user: str             # 来源用户
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
```

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
| **主对话 Agent** | 调用向量检索 Agent 加载记忆，拼装上下文 |
| **记忆分析 Agent** | 创建新记忆，设定 memory_type/importance/decay_rate |
| **记忆分析 Agent** | 评估现有记忆，更新 priority/is_forgotten |
| **向量检索 Agent** | 执行 embedding 检索 + ChromaDB 存储 |
| **关系分析 Agent** | 更新关系状态，影响记忆的 visibility 决策 |
| **消息提取** | 提供新内容供记忆分析 Agent 处理 |

---

## 10. 版本历史 (Version History)

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1.0 | 2026-03-24 | 初始设计（三级记忆模型 + 哈希去重） |
| v0.2.0 | 2026-07-11 | 重构：Agent 驱动决策替代固定公式；双类型系统升级为 Agent 判断；embedding 语义检索替代哈希去重；新增双层衰减（公式兜底 + Agent 覆盖）；ChromaDB + SQLite 双层存储；LangGraph checkpoint 作为短期记忆 |

---

> **维护者提示**:
> - 记忆系统是 Mnemosync 的灵魂，任何修改需谨慎。
> - 永久记忆限额是保护机制，不要随意提高。
> - 衰减参数需要实际测试调优，Agent 的 CoT 维度权重尤其需要实验验证。
> - embedding 模型的选择影响检索质量，切换模型需重新生成全量向量。"