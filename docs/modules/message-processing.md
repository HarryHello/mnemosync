# 消息处理流程 | Message Processing Flow

> **系统版本**: v0.3.0
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-29
> **最后更新**: 2026-07-26
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
       _verify_api_key → 身份解析 _resolve_identity_context (v0.3.0)
         │                   │
         │  (无策略/解析失败 → 非归属模式: actor_id=None, 不读写私有记忆)
         │                   │
         ▼                   ▼
       幂等预检 _lookup_idempotency (v0.3.0)
         │  (命中 → 重放首次响应, 零 LLM 开销)
         │
         ▼
       _resolve_source_frontend (from api_key.note)
       resolve main/embedding/rerank candidates (role_bindings)
       load persona (server-first) → prompt_cleaning on client system
              │
       ┌──────┴──────┐
       ▼             ▼
  非流式          流式 (生产主路径)
  graph.ainvoke   直接在 forward.py 内:
       │             1. 加载永久记忆 (SQLite, space_id 分区)
       │             2. 语义检索 (Chroma + rerank, RetrievalContext 受众过滤)
       │             3. 加载关系状态 (persona_id, source_user)
       │             4. render_main_dialogue_system(...) → system_text
       │             5. build_short_term_history(...)  ← v0.2.6 双窗, space_id
       │                (读 conversation_turns, 时间窗+模型窗裁剪)
       │             6. build_main_dialogue_messages(system, history, new_user)
       │             7. Forwarder.chat_stream → SSE 透传给客户端
       │             8. 流结束:
       │                - conversation_store.append(user, ..., actor_id, space_id, external_event_id)
       │                - conversation_store.append(assistant, ..., actor_id, space_id)
       │                - _record_idempotency (首次响应落库)
       │                - create_task(_run_memory_graph)
       │                          │
       └──────────────► 后台图 (关系分析 + 记忆分析):
                          extract user turn → 
                          (relationship_analysis ∥ memory_analysis) → END
```

**关键差异**:
- 非流式: `graph.ainvoke(initial_state)` 跑完整个图, 记忆加载/装填在 `main_dialogue_node` 内部完成
- 流式: 记忆加载 + 短期记忆装填 + 流式转发直接内联在 `forward.py._handle_stream`; 主对话完成后同步写 `conversation_turns` 两条 (user + assistant), 再把 collected_chunks 交给后台图跑关系/记忆分析

---

## 3. 阶段 1: 接收与预处理

**位置**: [forward.py `create_chat_completion`](../../src/api/routes/forward.py)

```
1. API Key 验证 (_verify_api_key): Authorization: Bearer sk-<key>
   → SqliteApiKeyStore.get_by_raw_key → api_key record (含 strategy_id, v0.3.0)
2. 身份解析 (_resolve_identity_context, v0.3.0):
   → 从 api_key.strategy_id 获取策略 (direct / api_key_bound / regex / llm)
   → IdentityResolver.resolve(...) → IdentityContext
   → 解析成功: actor_id / effective_user_id / space_id / channel_type / external_event_id
   → 解析失败/无策略: 非归属模式 — source_user = request.user or None, 不创建 Actor, 不读写私有记忆
3. 幂等预检 (_lookup_idempotency, v0.3.0):
   → 按 (api_key.id, external_event_id) 查幂等缓存
   → 命中: 直接重放首次响应 (零 LLM 开销, 零记忆副作用)
   注意: 幂等预检在提示词清洗和上游调用之前, 确保重复请求零成本
4. source_frontend 派生 (_resolve_source_frontend):
   = api_key.note (服务器派生, 不信任客户端 header)
5. 模型解析: RoleResolver
   → main_candidate / assist_candidate / embedding_candidate / rerank_candidate
   → 每个 ResolvedCandidate 携带 base_url / api_key / model / context_length / embedding_dim
6. 消息序列化: request.messages → messages_dict
   ⚠️ 只取最后一条 user 消息作为 new_user_content, 客户端携带的历史全部忽略 (v0.2.6)
7. 加载服务器人格 (settings.persona) — 服务器权威
8. 客户端 system 消息走 prompt_cleaning Agent 剥离人格描述, 保留功能性指令
9. 构建 initial_state:
   {
     new_user_content, source_user, actor_id,
     persona, persona_name, persona_id="default",
     main_candidate, embedding_candidate, rerank_candidate,
     proxy_thinking_enabled: 由 reasoning_control 决策,
     stream_mode: bool,
     source_frontend, space_id, channel_type,
     external_event_id, api_key_id,
   }
```

**⚠️ 服务器优先 (server-first) 人格**: Mnemosync 的人格由服务器 `[persona]` 段权威定义。客户端 system 消息不被信任为人格定义, 而是走 `prompt_cleaning` Agent (由 `sentence_classifier` 逐句分类) 剥离角色扮演描述、保留功能约束 (工具约束/格式要求/response_format 等)。详见 [architecture.md](../architecture.md) §2 与 [dev-decisions.md](../dev-decisions.md) v0.2.1。

**⚠️ 忽略客户端历史 (v0.2.6)**: 每次请求只有**最后一条 user 消息**参与本轮生成; 上下文的历史部分完全由服务端 `conversation_turns` 提供。原因: 不能依赖客户端传对——AstrBot 群聊场景每轮只传当前一句, 有的客户端每轮传完整历史, 用户还可能"清空对话"。见 [dev-decisions.md 跨前端短期记忆](../dev-decisions.md)。

**代理推理**: 由 [src/api/reasoning_control.py](../../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 判定, 4 条规则 (tools → 原生识别 → 前台点名推理 → 默认开关)。详见 [agents.md](agents.md) §4。

**延迟约束**: < 20ms (SQLite 走 aiosqlite)。

---

## 4. 阶段 2: 主对话

### 4.1 流式路径 (生产主路径)

**位置**: [forward.py `_handle_stream`](../../src/api/routes/forward.py)

```
1. 加载永久记忆
   memory_store.list_permanent(source_user, limit=permanent_load_top, space_id=space_id)
   → AudienceFilter.filter(perms, retrieval_ctx) 受众过滤
2. 语义检索
   query = new_user_content
   retrieval_ctx = RetrievalContext(effective_user_id, actor_id, space_id, channel_type, relationship)
   MemoryRetriever(forwarder, vector_store, memory_store).search(
     query, top_k=retrieval_top_k, retrieval_ctx=retrieval_ctx,
   )
   内部: embedding API → Chroma $or 粗筛 → is_visible 精筛 → rerank API → top_k
3. 更新访问时间: memory_store.mark_accessed(id)
4. 加载关系状态: memory_store.get_relationship(persona_id, source_user)
5. 装填 system 内容
   system_text = render_main_dialogue_system(
     persona_name, persona_prompt, user_name,
     permanent_memories, retrieved_memories, relationship,
     proxy_thinking_result,
   )
6. 装填短期记忆 (v0.2.6, v0.3.0 加 space_id)
   built = await build_short_term_history(
     store=conversation_store,
     now=now,
     window_days=settings.storage.short_term_days,
     context_length=main_candidate.context_length,
     system_text=system_text,
     new_user_text=new_user_content,
     max_tokens_hint=request.max_tokens,
     space_id=space_id,
   )
   → built.history: list[dict[role, content]]
7. 组装 messages
   messages = build_main_dialogue_messages(system_text, built.history, new_user_content)
8. 流式转发
   async for chunk in forwarder.chat_stream(messages, ..., candidate=main_candidate):
     collected_chunks.append(chunk)
     yield chunk                # 零缓冲透传
9. 流结束
   await conversation_store.append("user", new_user_content,
                                    token_count=..., source_frontend=source_frontend,
                                    actor_id=actor_id, space_id=space_id, external_event_id=external_event_id)
   await conversation_store.append("assistant", collected_response_text,
                                    token_count=..., source_frontend=source_frontend,
                                    actor_id=actor_id, space_id=space_id)
   await _record_idempotency(...)  # 首次响应落幂等缓存
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
| v0.2.6 | 2026-07-18 | 数据流补充服务端 `conversation_turns` 装填与回写; 客户端历史被忽略, 仅取最后一条 user; source_frontend 从 api_key.note 派生; ResolvedCandidate 传 context_length 给双窗算法 |
| v0.3.0 | 2026-07-26 | 新增身份解析与幂等预检步骤; source_user 改为从 identity_ctx.effective_user_id 派生 (非归属模式为 None, 不再硬编码 "default"); initial_state 新增 actor_id/space_id/channel_type/persona_id/external_event_id/api_key_id; 记忆检索接入 RetrievalContext 受众过滤; 短期记忆装填与流水回写接入 space_id 分区 |
