# 消息处理流程 | Message Processing Flow

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 概述

从前端请求到达 `/v1/chat/completions`, 到用户收到回复、后台完成记忆入库的完整轨迹。所有实际编排在 [src/api/routes/forward.py](../../src/api/routes/forward.py) 与 [src/core/graph/](../../src/core/graph/) 中。

### 1.1 三段式生命周期

| 阶段 | 是否阻塞客户端 | 主要动作 |
|------|--------------|---------|
| 阶段 1: 接收与预处理 | 是 | API Key 验证, 消息标准化, 构建 initial state |
| 阶段 2: 主对话 | 是 | 加载记忆 + 拼上下文 + 上游模型生成回复 |
| 阶段 3: 记忆图 | 否 (流式后台) | 记忆分析 ∥ 关系分析, 向量入库 |

流式请求下阶段 3 用 `asyncio.create_task` 后台跑; 非流式请求下阶段 2+3 合并成一次 `graph.ainvoke`。

---

## 2. 流程总览

```
Client ──► POST /v1/chat/completions
              │
              ▼
       [forward.py]
       API Key 验证 → source_user → 加载服务器 persona → initial_state
              │
       ┌──────┴──────┐
       ▼             ▼
  非流式          流式 (生产主路径)
  graph.ainvoke   直接在 forward.py 内:
       │             1. 加载永久记忆 (SQLite)
       │             2. 语义检索 (Chroma + rerank)
       │             3. 加载关系状态
       │             4. build_main_dialogue_messages()
       │             5. Forwarder.chat_stream → SSE 透传给客户端
       │             6. 流结束: create_task(_run_memory_graph)
       │                          │
       └──────────────► 记忆图 (含所有 5 个节点):
                          parse_request → main_dialogue →
                          (relationship_analysis ∥ memory_analysis) → END
```

**关键差异**:
- 非流式: `graph.ainvoke(initial_state)` 跑完整个图, 阶段 2 的记忆加载/拼装在 `main_dialogue_node` 内部完成
- 流式: 阶段 2 的记忆加载与流式转发在 `forward.py._handle_stream` 内直接完成 (不走图), 然后把 collected_chunks 一起塞进 state 交给后台图完成阶段 3

---

## 3. 阶段 1: 接收与预处理

**位置**: [forward.py `create_chat_completion`](../../src/api/routes/forward.py)

```
1. API Key 验证: Authorization: Bearer sk-<key>
   → SqliteApiKeyStore.get_by_raw_key → api_key_id
2. 模型白名单: request.model == "mnemosync-any" 或为空, 否则 400
3. 消息序列化: request.messages → messages_dict
4. 提取 source_user (request.user 或 "default")
5. 加载服务器人格 (⚠️ 服务器优先, 不从客户端 system 消息提取)
   → 人格存储于服务器端 (当前阶段为 config 或硬编码,
      未来迁移至 personas 表 + Admin API)
6. 构建 initial_state:
   {
     messages, source_user, persona, persona_name,
     proxy_thinking_enabled: 由 reasoning_control 决策,
     stream_mode: bool
   }
```

**⚠️ 服务器拥有 (server-first) 人格设计**: Mnemosync 的人格由服务器端权威定义, 不作为客户端请求的一部分。客户端 system 消息中的内容**当前被丢弃** (不从中提取 persona); 未来计划用辅助模型分析客户端 system 消息, 剥离出功能性指令 (tool 约束/response_format 等) 保留, 人格描述部分丢弃。详见 [architecture.md](../architecture.md) §2。

**代理推理**: 由 [src/api/reasoning_control.py](../../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 判定, 4 条规则 (tools → 原生识别 → 前台点名推理 → 默认开关)。详见 [agents.md](agents.md) §4。

**延迟约束**: < 20ms (SQLite 走 aiosqlite)。

---

## 4. 阶段 2: 主对话

### 4.1 流式路径 (生产主路径)

**位置**: [forward.py `_handle_stream`](../../src/api/routes/forward.py)

```
1. 加载永久记忆
   memory_store.list_permanent(source_user, limit=permanent_load_top)
2. 语义检索
   query = 最新 user 消息
   MemoryRetriever(forwarder, vector_store, memory_store).search(
     query, top_k=retrieval_top_k, source_user=...
   )
   内部: embedding API → Chroma cosine 粗筛 → rerank API → top_k
3. 更新访问时间: memory_store.mark_accessed(id)
4. 加载关系状态: memory_store.get_relationship("default", source_user)
5. 拼装上下文
   build_main_dialogue_messages(
     persona_prompt, persona_name, user_name,
     permanent_memories, retrieved_memories, relationship,
     conversation_history,
   )
6. 流式转发
   async for chunk in forwarder.chat_stream(messages_with_memory, ...):
     collected_chunks.append(chunk)
     yield chunk                # 零缓冲透传
7. 流结束
   asyncio.create_task(_run_memory_graph(initial_state, collected_chunks))
```

**上游超时**: `ForwarderConfig(timeout=90.0)`。

**注意**: 流式路径不走 `parse_request_node` 也不走 `main_dialogue_node`, 而是把这两步的逻辑直接内联在 `_handle_stream` 里, 换取更低的 TTFT (省一次 `graph.ainvoke` 的开销)。

### 4.2 非流式路径

**位置**: [forward.py `_handle_non_stream`](../../src/api/routes/forward.py)

```
final_state = await graph.ainvoke(initial_state)
response_text = final_state["response"]
→ 组装 ChatCompletionResponse 返回
```

图内部完整跑过 `parse_request → main_dialogue → (relationship_analysis ∥ memory_analysis) → END`, 记忆分析与关系分析同步完成后才返回。

---

## 5. 阶段 3: 记忆图 (异步)

### 5.1 触发方式

- **流式**: `asyncio.create_task(_run_memory_graph(initial_state, collected_chunks))`, 客户端已断开
- **非流式**: `graph.ainvoke` 内自然衔接

### 5.2 后台图执行

`_run_memory_graph` 把 collected_chunks 拼回 `response_text` 塞进 state, 再 `graph.ainvoke`:

```
parse_request         # 提取本轮新消息
      │
      ▼
main_dialogue         # 流式模式下 response 已填, 这一步幂等
      │
      ├──────────────────────┐  (并行)
      ▼                      ▼
relationship_analysis   memory_analysis (ReAct)
                              │
                              └─► MemoryLifecycle.store_candidate()
                                    ├─► embedding → Chroma.add
                                    └─► SQLite INSERT MemoryEntry
```

**记忆分析** (ReAct, max_iterations=6): 用 `vector_search / emotion_analyzer / time_decay_calculator` 三个工具, 输出 `{new_memories, decay_evaluations}`。写入由节点内的 `MemoryLifecycle` 顺手完成——**没有独立的 vector_index 节点**。

**关系分析** (ReAct, max_iterations=3): 用 `emotion_analyzer`, 输出 `{intimacy_delta, trust_delta, new_relationship_type}`。

详见 [agents.md](agents.md) §3/§5。

---

## 6. 错误处理

| 失败位置 | 影响 | 处理 |
|---------|------|------|
| API Key 无效 | 阻塞 | 401 |
| 模型白名单不通过 | 阻塞 | 400 |
| 永久记忆加载失败 | 主对话降级 | 记 log, 用空列表继续 |
| 语义检索失败 | 主对话降级 | 记 log, 只用永久记忆 |
| 上游 SSE 超时 | 阻塞 | 流内注入 error data |
| 上游 SSE 错误 | 阻塞 | 流内注入 error data |
| 记忆分析失败 | 本次不入库 | 记 log, 关系分析继续 |
| 关系分析失败 | 关系不更新 | 记 log |
| 后台图整体失败 | 无用户可见影响 | logging.warning |

原则: **主路径失败 → 直接告知客户端。后台失败 → 记 log, 不重试。**

---

## 7. 性能预算

| 项 | 目标 | 说明 |
|----|------|------|
| API Key 验证 | < 10ms | SQLite 主键查询 |
| 永久记忆加载 | < 10ms | SQLite 索引查询 |
| 嵌入 + rerank 检索 | < 300ms | 服务商 API + 本地 Chroma |
| 上下文拼装 | < 5ms | 内存操作 |
| TTFT (含上游) | < 1s | 上游模型响应决定下界 |
| 后台记忆图 | 1-5s | 与客户端无关 |

---

## 8. 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [Forwarder](forward.md) | 阶段 2 流式转发、阶段 3 内 Agent LLM 调用 |
| [LangGraph](langgraph.md) | 阶段 3 (非流式含阶段 2) 的编排骨架 |
| [多 Agent 设计](agents.md) | 记忆分析、关系分析、主对话的详细规格 |
| [记忆系统](memory-system.md) | Chroma + SQLite 底层存储与检索 |
| [API Key 管理](api-key.md) | 阶段 1 的鉴权来源 |
| [身份认证](auth.md) | 上层用户与 API Key 的关系 |

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-29 | 10 步线性管道 |
| v0.2.0 | 2026-07-12 | LangGraph 多 Agent 编排, ChromaDB, 代理思考 |
| v0.2.1 | 2026-07-16 | 纠正人格来源: 从"提取客户端 system 消息"改为"服务器加载人格" (server-first); 修正代理推理启用方式描述 |
