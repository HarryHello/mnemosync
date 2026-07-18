# 消息提取模块 | Message Extraction Module

> **系统版本**: v0.2.6
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-18
> **作者**: HarryHelloo

---

## 1. 定位 (v0.2.6 重要变化)

消息提取是 Mnemosync 的**基础设施 / 协议适配层**, 位于 [src/infra/extraction.py](../../src/infra/extraction.py)。

**v0.2.6 以前**: 主对话与后台记忆图**都**用 `extract_new_messages()` 从客户端传来的 `messages` 里切出"新内容"再决定后续动作。

**v0.2.6 起 (当前)**: 主对话装填路径**完全不再使用**消息提取。原因见 [dev-decisions.md 跨前端短期记忆](../dev-decisions.md):

> 不能依赖客户端传对: 有的客户端每轮只传当前一句 (AstrBot 群聊场景), 有的每轮传完整历史, 用户还可能主动清空 — 中间件不能修改客户端行为, 只能自己维护真相。

主对话路径 (forward.py) 直接**只取客户端 messages 里的最后一条 user**, 作为 `new_user_content` 传给上游, 历史全部由服务端 `conversation_turns` 双窗装填 (见 [memory-system.md §1.4](memory-system.md#14-短期记忆-v026--跨前端对话流水))。

**本模块当前仅剩一个消费者**: 后台记忆图 (`parse_request` → `memory_analysis`) 的 `extracted_new` 字段仍走 `extract_new_messages()`, 从本轮 messages 里挑记忆分析要看的用户 turn。这个路径不会阻塞客户端, 且记忆抽取本身对"漏一句还是重一句"容错度较高, 保留即可。

未来若确认后台图完全能靠"最后一条 user"就够 (等短期记忆双窗打透后应是够的), 会移除本模块。

---

## 2. 为什么保留 (只在后台图)

**后台记忆图的输入需求**:

- `parse_request_node` 收到本轮完整 messages, 需要挑出"这一轮用户真正说了什么"
- 记忆分析 Agent 想看的是"新 user turn", 不希望把已抽过记忆的历史再抽一遍
- 后台图不在主路径上, `extract_new_messages` 的 O(n·m) 遍历延迟不敏感

因此 `extract_new_messages(messages, server_history)` 的语义在后台图里仍然合理。

**注意**: `server_history` 现在从 `conversation_turns` 而非 LangGraph checkpoint 拉取; 精确匹配用的是 `(role, content)` 二元组。

---

## 3. 实现

见 [src/infra/extraction.py](../../src/infra/extraction.py):

```python
def extract_new_messages(
    messages: list[dict],
    server_history: list[dict],
) -> list[dict]:
    """从客户端 messages 中提取"没在 server_history 出现过"的部分。

    精确匹配 (role + content), 用于后台记忆图挑选待抽取的 user turn。
    时间复杂度 O(n·m); 因不在主路径, 可接受。
    """
    ...
```

### 3.1 为什么不用 embedding

- **精确切分 vs 语义匹配是两个问题**: 前者要"这条 exact 消息之前存过吗", 后者交给 `MemoryRetriever` 的 embedding + rerank
- 记忆分析 Agent 会用 `vector_search` 工具做语义查重, 本模块不该重复这一职责

---

## 4. 在数据流中的位置 (v0.2.6)

```
主路径 (主对话, 阻塞客户端):
    客户端 messages → [取最后一条 user] → new_user_content
                                              │
                                              ▼
                                     build_short_term_history()
                                     (从 conversation_turns 双窗裁剪)
                                              │
                                              ▼
                                     build_main_dialogue_messages()
                                              │
                                              ▼
                                     Forwarder.chat_stream()
                                     ↑↑↑ 主路径完全不经消息提取 ↑↑↑

后台图 (记忆分析, 不阻塞客户端):
    initial_state.messages → parse_request_node
                              │
                              ▼
                        extract_new_messages(messages, server_history)
                              │
                              ▼
                        state["extracted_new"] → memory_analysis / relationship_analysis
```

---

## 5. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-24 | 初始设计: 消息提取 + 哈希去重, 作为 Context Pipeline 的起点 |
| v0.2.0 | 2026-07-12 | 定位为基础设施/协议适配层; 与记忆分析 Agent 职责分离 |
| v0.2.6 | 2026-07-18 | 主对话装填不再依赖本模块 (改为服务端 `conversation_turns` 双窗装填); 本模块降级为后台记忆图专用 |

---

> **维护者提示**:
> - 本模块是确定性算法, 不引入任何网络调用或 LLM 调用
> - 语义层面的去重/关联由记忆分析 Agent 通过 vector_search 工具完成
> - 主对话装填不能再依赖本模块 — 客户端历史不可信, 见 [dev-decisions.md 跨前端短期记忆](../dev-decisions.md)
