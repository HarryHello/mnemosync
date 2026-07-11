# 工具设计 | Tools Design

> **系统版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-07-11
> **作者**: HarryHelloo

---

## 1. 概述 (Overview)

本文档描述 Mnemosync 多 Agent 系统中的 3 个工具的设计与实现。每个工具封装为 LangChain Tool 对象，注册到对应 Agent 供其通过 Function Call 调用。

### 1.1 工具全景

| # | 工具名 | 调用者 | 底层服务 | 函数签名 |
|---|--------|--------|----------|----------|
| 1 | `vector_search` | 主对话 Agent、记忆分析 Agent | DashScope embedding + ChromaDB + DashScope rerank | `(query, top_k, source_user) → list[MemoryEntry]` |
| 2 | `emotion_analyzer` | 记忆分析 Agent、关系分析 Agent | DashScope qwen-turbo | `(text) → EmotionResult` |
| 3 | `time_decay_calculator` | 记忆分析 Agent | 本地计算（纯函数） | `(memory_id) → DecayResult` |

---

## 2. 工具 1: 向量语义检索工具 (`vector_search`)

### 2.1 功能

以自然语言查询为输入，返回语义最相似的历史记忆列表。

### 2.2 调用方

| Agent | 使用场景 |
|-------|----------|
| **主对话 Agent** | 收到用户新消息后，检索相关记忆以拼入上下文 |
| **记忆分析 Agent** | 判断新信息是否与已有记忆重复/冲突/关联 |

### 2.3 实现流程

```
vector_search(query: str, top_k: int = 5, source_user: str | None = None)
    │
    ▼
1. query → DashScope text-embedding-v3 → query_vector
    │  POST https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding
    │  参数: model="text-embedding-v3", input=query, dimension=768
    │  返回: {embedding: [0.23, -0.15, ...]}
    │
    ▼
2. ChromaDB.similarity_search(query_vector, n_results=top_k * 2, filter=source_user)
    │  余弦相似度 (cosine distance)
    │  可选过滤: 只检索 source_user 的记忆（或 source_restricted）
    │  返回: top 10 候选 (id, content, similarity_score, metadata)
    │
    ▼
3. top 10 候选 → DashScope gte-rerank → 精排 top_k
    │  POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
    │  参数: model="gte-rerank", query=query, documents=candidates
    │  返回: {results: [{index: 2, relevance_score: 0.94}, ...]}
    │
    ▼
4. 按 relevance_score 降序，取 top_k 条
    │
    ▼
5. 通过 memory_id 从 SQLite 拉取完整 MemoryEntry 字段
    │
    ▼
6. 返回 list[MemoryEntry]（含 content, importance, emotional_tags, ...）
```

### 2.4 性能约束

| 阶段 | 预期延迟 | 说明 |
|------|----------|------|
| embedding API | ~50-100ms | DashScope 网络延迟 |
| ChromaDB 粗筛 | ~1-5ms | 本地操作，很快 |
| reranker API | ~100-200ms | 批量 10 条，需传输文本 |
| SQLite 补全 | ~1-5ms | 本地查询 |
| **总计** | **~150-300ms** | 需在 TTFT 约束内 |

> 权衡：如果 TTFT 压力大，可跳过 reranker（只用 embedding + cosine 粗筛）。此时精度下降但总延迟降至 ~50-100ms。

### 2.5 ChromaDB Collection 设计

```python
import chromadb

client = chromadb.PersistentClient(path="./data/chroma")

collection = client.get_or_create_collection(
    name="mnemosync_memories",
    metadata={"hnsw:space": "cosine"}
)

# 添加记忆（入库时）
collection.add(
    ids=[entry.id],
    embeddings=[vector],  # 768 维
    metadatas=[{
        "content": entry.content,
        "source_user": entry.source_user,
        "importance": entry.importance,
        "memory_type": entry.memory_type.value,
        "emotional_tags": ",".join(entry.emotional_tags),
        "created_at": entry.created_at.isoformat(),
    }]
)
```

### 2.6 LangChain Tool 封装

```python
from langchain_core.tools import tool

@tool
def vector_search(query: str, top_k: int = 5, source_user: str | None = None) -> list[dict]:
    """搜索与查询文本语义最相似的历史记忆。

    Args:
        query: 查询文本（通常是用户最新消息）
        top_k: 返回结果数量，默认 5
        source_user: 可选，限定来源用户

    Returns:
        相关记忆列表，每条包含 content, importance, emotional_tags, similarity
    """
    ...
```

---

## 3. 工具 2: 情绪分析工具 (`emotion_analyzer`)

### 3.1 功能

调用辅助小模型（qwen-turbo）分析文本的情绪内容，输出情绪标签、强度和类别。

### 3.2 调用方

| Agent | 使用场景 |
|-------|----------|
| **记忆分析 Agent** | ReAct 循环中分析对话内容的情感标签 |
| **关系分析 Agent** | 分析对话中的情感信号，辅助亲密度计算 |

### 3.3 实现流程

```
emotion_analyzer(text: str) → EmotionResult
    │
    ▼
1. 构建分析 prompt（系统指令 + 目标文本）
    │
    ▼
2. 调用 DashScope qwen-turbo (assist model)
    │  temperature=0.1（低温度保证一致性）
    │  response_format=json_object
    │
    ▼
3. 解析返回 JSON
    │
    ▼
4. 返回结构化 EmotionResult
```

### 3.4 System Prompt

```
你是情绪分析助手。分析以下文本的情绪内容，以 JSON 格式返回：

{
  "emotion": "happy|sad|angry|anxious|neutral|excited|grateful|stressed",
  "intensity": 0.0-1.0,
  "category": "casual_chat|health_disclosure|personal_sharing|preference_statement|emotional_expression|complaint|gratitude|other",
  "keywords": ["关键词1", "关键词2"],
  "summary": "一句话概括情绪内容"
}

规则：
- emotion: 主要情绪，无法判断用 neutral
- intensity: 情绪强度，闲聊 0.1-0.3，强烈情绪 0.7-1.0
- category: 对话类型分类
- 不要过度解读：只分析明确表达的情绪
```

### 3.5 输出结构

```python
@dataclass
class EmotionResult:
    emotion: str           # happy | sad | angry | anxious | neutral | excited | grateful | stressed
    intensity: float       # 0.0 ~ 1.0
    category: str          # casual_chat | health_disclosure | personal_sharing | ...
    keywords: list[str]    # 情绪关键词
    summary: str           # 一句话概括
```

### 3.6 LangChain Tool 封装

```python
from langchain_core.tools import tool

@tool
def emotion_analyzer(text: str) -> dict:
    """分析文本的情绪内容，返回情绪标签、强度和类别。

    Args:
        text: 需要分析的用户消息或对话片段

    Returns:
        {emotion, intensity, category, keywords, summary}
    """
    ...
```

---

## 4. 工具 3: 时间衰减计算工具 (`time_decay_calculator`)

### 4.1 功能

给定一条记忆，计算其时间维度的衰减状态：理论优先级、半衰期状态、距离遗忘的天数。

与其他两个工具不同，这是一个**纯本地计算函数**，不调用任何外部 API。

### 4.2 调用方

| Agent | 使用场景 |
|-------|----------|
| **记忆分析 Agent** | 衰减评估的公式基线步骤 |

### 4.3 计算公式

```
理论优先级 = importance × 衰减因子 × 过期惩罚 + 访问加成

其中:
- 衰减因子 = 0.5 ^ (经过天数 / 半衰期天数)
- 半衰期天数 = decay_rate_to_half_life(decay_rate)
- 过期惩罚 = 0.01 (如果 expires_at < now) 或 1.0
- 访问加成 = log(access_count + 1) × 0.05

decay_rate → 半衰期映射:
  decay_rate 0.0  → ∞ (永不过期)
  decay_rate 0.05 → 182 天
  decay_rate 0.1  → 91 天
  decay_rate 0.3  → 33 天
  decay_rate 0.5  → 51 天
  decay_rate 0.7  → 17 天
  decay_rate 0.9  → 11 天
```

### 4.4 输出结构

```python
@dataclass
class DecayResult:
    memory_id: str             # 记忆 ID
    days_elapsed: int          # 创建至今的天数
    half_life_days: int | None # 半衰期天数（None = 永不过期）
    time_factor: float         # 衰减因子 0.0-1.0
    expiration_penalty: float  # 过期惩罚 0.01 或 1.0
    access_bonus: float        # 访问加成
    theoretical_priority: float # 理论优先级
    days_to_forgotten: int | None # 距离优先级降到 0.05 以下的天数（None = 不会遗忘）
    current_state: str         # ACTIVE | DORMANT | WEAK | FORGOTTEN
```

### 4.5 实现

```python
from math import log
from datetime import datetime, timezone

# decay_rate → 半衰期天数映射
DECAY_RATE_TO_HALF_LIFE = {
    0.0: None,      # 永不过期
    0.05: 182,
    0.1: 91,
    0.3: 33,
    0.5: 51,
    0.7: 17,
    0.9: 11,
}

def _decay_rate_to_half_life(decay_rate: float) -> int | None:
    """将 decay_rate 映射到半衰期天数。使用最接近的预设值。"""
    if decay_rate <= 0.0:
        return None
    closest = min(DECAY_RATE_TO_HALF_LIFE.keys(),
                  key=lambda k: abs(k - decay_rate))
    return DECAY_RATE_TO_HALF_LIFE[closest]

def _priority_to_state(priority: float) -> str:
    if priority > 0.3:
        return "ACTIVE"
    elif priority > 0.1:
        return "DORMANT"
    elif priority > 0.05:
        return "WEAK"
    else:
        return "FORGOTTEN"

def calculate_decay(entry_id: str, importance: float, decay_rate: float,
                    access_count: int, created_at: datetime,
                    expires_at: datetime | None = None) -> DecayResult:
    """计算记忆的时间衰减状态。纯函数，无副作用。"""
    now = datetime.now(timezone.utc)
    days_elapsed = max(0, (now - created_at).days)

    half_life = _decay_rate_to_half_life(decay_rate)

    # 衰减因子
    if half_life is None or half_life == 0:
        time_factor = 1.0
    else:
        time_factor = 0.5 ** (days_elapsed / half_life)

    # 过期惩罚
    if expires_at and now > expires_at:
        expiration_penalty = 0.01
    else:
        expiration_penalty = 1.0

    # 访问加成
    access_bonus = log(access_count + 1) * 0.05

    # 理论优先级
    theoretical_priority = importance * time_factor * expiration_penalty + access_bonus
    theoretical_priority = min(1.0, theoretical_priority)  # 上限 1.0

    # 距离遗忘的天数（简化估算：假设不再被访问）
    days_to_forgotten = None
    if half_life is not None and importance > 0:
        # 解方程: importance * 0.5^(x/half_life) < 0.05
        # x > half_life * log2(importance / 0.05)
        try:
            days_to = half_life * (log(importance / 0.05) / log(2))
            days_to_forgotten = int(days_to - days_elapsed)
        except (ValueError, ZeroDivisionError):
            days_to_forgotten = None

    return DecayResult(
        memory_id=entry_id,
        days_elapsed=days_elapsed,
        half_life_days=half_life,
        time_factor=round(time_factor, 4),
        expiration_penalty=expiration_penalty,
        access_bonus=round(access_bonus, 4),
        theoretical_priority=round(theoretical_priority, 4),
        days_to_forgotten=days_to_forgotten,
        current_state=_priority_to_state(theoretical_priority),
    )
```

### 4.6 LangChain Tool 封装

```python
from langchain_core.tools import tool

@tool
def time_decay_calculator(memory_id: str) -> dict:
    """计算给定记忆的时间衰减状态，返回理论优先级和各维度分解值。

    这是纯公式计算，不包含 Agent 的多维度评估。
    Agent 应在此基线值之上执行 CoT 分析。

    Args:
        memory_id: 记忆条目的唯一 ID

    Returns:
        {memory_id, days_elapsed, half_life_days, time_factor,
         expiration_penalty, access_bonus, theoretical_priority,
         days_to_forgotten, current_state}
    """
    ...
```

---

## 5. 工具注册与调用

### 5.1 LangGraph 中的工具绑定

```python
from langgraph.prebuilt import ToolNode

# 工具注册
all_tools = [vector_search, emotion_analyzer, time_decay_calculator]

# 主对话 Agent 绑定的工具
main_dialogue_tools = [vector_search]

# 记忆分析 Agent 绑定的工具
memory_analysis_tools = [vector_search, emotion_analyzer, time_decay_calculator]

# 代理思考 Agent 绑定的工具
proxy_thinking_tools = [vector_search, emotion_analyzer]

# 关系分析 Agent 绑定的工具
relationship_analysis_tools = [emotion_analyzer]

# 在 StateGraph 中创建 tool node
tool_node = ToolNode(all_tools)
```

### 5.2 Agent 与工具的交互模式

```
Agent Node                    Tool Node
    │                             │
    ├─ LLM 决定调用 tool           │
    ├─ 输出 function_call ─────→   │
    │                          ├─ 执行工具
    │                          ├─ 返回 function_result
    │  ←── function_result ────┘   │
    ├─ LLM 处理结果                │
    ├─ 继续推理或输出最终结果       │
```

---

## 6. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.2.0 | 2026-07-11 | 初始版本：3 个工具（向量检索、情绪分析、时间衰减计算） |

---

> **维护者提示**:
> - `vector_search` 是性能瓶颈所在（~150-300ms），需关注 DashScope API 延迟。
> - `emotion_analyzer` 依赖小模型推理质量，prompt 需要根据实际效果迭代。
> - `time_decay_calculator` 是纯函数，输出是 Agent 的参考基线而非最终决策。
> - 嵌入模型切换时需重新生成 ChromaDB 全量向量，建议做好版本标记。