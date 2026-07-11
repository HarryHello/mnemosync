# 消息提取模块 | Message Extraction Module

> **系统版本**: v0.2.0
> **文档状态**: 设计中
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-12
> **作者**: HarryHelloo

---

## 1. 定位 (Positioning)

消息提取是 Mnemosync 的 **基础设施 / 协议适配层**，不属于 Agent 系统。

**为什么需要它？**

OpenAI API 的设计中，`messages` 是对话上下文的载体，前端为了保持连贯性会将完整历史一起发送。Mnemosync 需要从中分离出"真正的新内容"——这是协议兼容问题，不是智能决策问题。

```
前端发来的 messages = [历史1, 历史2, 历史3, ..., 新消息]
                            ↑
                  消息提取：切掉已存储的历史，只留新内容
                            ↓
                  新内容 → 记忆分析 Agent（智能决策）
```

| | 消息提取 | 记忆分析 Agent |
|---|---|---|
| **输入** | 前端传来的完整 messages 列表 | 提取后的新内容 |
| **做什么** | 切分历史/新内容 | 判断是否值得记住、打标签、定等级 |
| **推理方法** | 无（确定性匹配） | ReAct |
| **本质** | **协议适配层** | **智能决策** |
| **延迟约束** | 必须极快（< 10ms） | 可异步执行 |

---

## 2. 设计哲学 (Design Philosophy)

### 2.1 核心理念

**Mnemosync 是记忆的唯一源头，前端不需要记得历史。**

```
┌─────────────────────────────────────────────────────────────┐
│  Mnemosync 的记忆同步原理                                    │
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

### 2.2 为什么需要提取新内容？

**场景 1：前端 B 不知道前端 A 说过的话**

```
T1: 前端 A (AstrBot) - 用户说"我叫马达"
    → Mnemosync 存储这条记忆

T2: 前端 B (AIRI) - 用户说"我最近压力大"
    → 前端 B 发送的上下文中没有 T1 的对话
    → 如果直接用前端上下文，上游不知道"马达"是谁

正确做法:
1. 提取 T2 的新内容 ("压力大")
2. Agent 通过 embedding 检索加载已有记忆 ("叫马达")
3. 主对话 Agent 合并后发送给上游
→ 上游回答："马达，听说你最近压力大？"
```

**场景 2：前端发送冗余上下文**

```
前端发送的上下文:
[user] 你好              ← 已存储
[assistant] 你好呀        ← 已存储
[user] 我叫马达          ← 已存储
[assistant] 很高兴认识你  ← 已存储
[user] 我最近压力大      ← 这才是真正的新内容

Mnemosync:
1. 识别前 4 条已经存储过了
2. 只保留"压力大"这条新内容
3. 交给记忆分析 Agent 处理
```

---

## 3. 实现原理 (Implementation)

### 3.1 核心思路：列表顺序匹配

按顺序遍历前端传来的 messages，与服务器存储的历史逐一比对，未匹配的部分即为新增内容。

### 3.2 算法

```python
def extract_new_messages(
    messages: list[dict],
    server_history: list[dict],
) -> list[dict]:
    """从前端 messages 中提取新增消息。

    Args:
        messages: 前端传来的完整消息列表
        server_history: 服务器已存储的历史消息

    Returns:
        新增消息列表（从未存储过的部分）
    """
    new_messages = []
    history_index = 0

    for msg in messages:
        # 在历史中查找匹配
        while history_index < len(server_history):
            hist_msg = server_history[history_index]
            if _messages_equal(msg, hist_msg):
                history_index += 1
                break  # 找到匹配，本条为历史消息
            history_index += 1
        else:
            # 历史已遍历完，剩余的都是新消息
            new_messages.append(msg)

    return new_messages


def _messages_equal(a: dict, b: dict) -> bool:
    """判断两条消息是否相等（精确匹配）。"""
    return (
        a.get("role") == b.get("role") and
        a.get("content") == b.get("content") and
        a.get("name", "") == b.get("name", "")
    )
```

### 3.3 为什么这里不用 embedding？

消息提取做的是**精确去重**——"这条 exact 消息前端发过没有？"——而非语义匹配。原因：

1. **延迟约束**：消息提取在请求主路径上，必须 < 10ms。embedding API 调用至少 50ms。
2. **语义区分由下游 Agent 负责**：消息是否和已有**记忆**相似 → 记忆分析 Agent 通过 vector_search 判断。
3. **职责分离**：提取是"切分"（确定性问题），分析是"理解"（语义问题）。

```
消息提取（精确匹配）         记忆分析 Agent（语义匹配）
  "我叫马达" vs "我叫马达"    "我对花生过敏" vs "我喜欢花生酱"
  → 精确相同，跳过             → 语义相关但不等同，Agent 判断
```

### 3.4 时间戳处理

| 场景 | 时间戳来源 |
|------|-----------|
| 前端传入 `timestamp` 字段 | 优先使用前端传入值 |
| 无显式时间戳 | 使用服务器接收时间 (`datetime.now()`) |
| 群聊多条消息 | 保持前端传入的相对时序 |

---

## 4. 在新架构中的位置

```
用户请求 → API Gateway
              │
              ├─ 1. 鉴权（API Key）
              ├─ 2. 消息提取 ← 本模块
              │      messages → {历史, 新内容}
              │
              ├─ 3. 新内容 → 主对话 Agent
              │                    │
              │                    ├─ vector_search（embedding 检索记忆）
              │                    ├─ 拼装上下文（人格 + 记忆 + checkpoint）
              │                    └─ 生成回复 → 流式返回
              │
              └─ 4. 异步：新内容 → 记忆分析 Agent → 衰减 Agent → 入库
```

消息提取是**整个系统的第一个处理步骤**，在鉴权之后、任何 Agent 调用之前完成。

---

## 5. 与 LangGraph 的集成

在 LangGraph StateGraph 中，消息提取作为第一个节点 `parse_request` 的一部分：

```python
def parse_request_node(state: AgentState) -> AgentState:
    """解析请求节点 — 基础设施，非 Agent。"""
    messages = state["messages"]

    # 协议适配：提取新内容
    new_messages = extract_new_messages(
        messages=messages,
        server_history=load_history(state["source_user"]),
    )

    state["extracted_new"] = new_messages
    return state
```

---

## 6. 版本历史 (Version History)

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v0.1.0 | 2026-03-24 | 初始设计：消息提取 + 哈希去重，作为 Context Pipeline 的起点 |
| v0.2.0 | 2026-07-12 | 重新定位为基础设施/协议适配层；明确与记忆分析 Agent 的职责边界；精确匹配保留，语义匹配交由 Agent |

---

> **维护者提示**:
> - 本模块是确定性算法，不应引入任何网络调用或 LLM 调用。
> - 语义层面的去重/关联由记忆分析 Agent 通过 vector_search 工具完成。
> - 消息提取与 embedding 检索在技术栈上互补：提取负责"是不是同一条"，检索负责"是不是同一个意思"。