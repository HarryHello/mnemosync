# 开发决策记录 | Development Decisions

> 本文档记录开发过程中遇到的问题、做出的决策、以及待你确认的事项.
> 你回来后请逐条过目, 标记同意或提出修改.

---

## 决策 1: Qwen3 thinking 模式与 JSON 输出的冲突

**日期**: 2026-07-12
**状态**: ✅ 已处理（待你确认方案）

### 问题

硅基流动的 `Qwen/Qwen3-8B` 默认开启 thinking 模式（输出 `<think>...</think>` 再给答案）。当使用 `response_format={"type": "json_object"}` 要求 JSON 输出时:
- 模型可能思考过久导致 30s 超时
- 返回的内容可能被 thinking 占用, 实际 JSON 字段为空（测试时 emotion=neutral, keywords=[]）

### 决策

1. **Forwarder.timeout 默认值从 30s 调到 60s**（给 thinking 留余量）
2. **emotion_analyzer / 记忆分析等需要结构化输出的 Agent, 在请求中传 `extra_body={"enable_thinking": False}`** 关闭 thinking
3. **主对话 Agent 保留 thinking**（输出质量更好, 且流式透传无影响）

### 你需要确认

- 是否同意关闭辅助 Agent 的 thinking?（关闭后响应更快, 但推理能力略降。对情绪分析/记忆提取这类结构化任务, 关闭更合适）
- 或者你想保留 thinking, 由我在解析时过滤 `<think>...</think>` 标签?

---

## 决策 2: 辅助模型选型

**日期**: 2026-07-12
**状态**: ✅ 已确认

### 背景

你最初填的 `assist_model = "Qwen/Qwen2.5-7B-Instruct"`. 测试发现它在硅基流动上**不支持 function_call**（探测脚本确认: Qwen2.5-7B 不发 tool_calls, 而是把调用当文本输出）.

支持 function_call 的候选: `Qwen/Qwen3-8B` / `Qwen/Qwen3-32B` / `Qwen/Qwen2.5-72B-Instruct`.

### 决策

按你的意愿"先期测试不需要太大模型", 改为 `assist_model = "Qwen/Qwen3-8B"`（与 main_model 同）.

### 你需要确认

- 后期若记忆分析 ReAct 循环不稳定, 再升级到 `Qwen3-32B` 作为辅助模型?

---

## 决策 3: 嵌入维度不写死

**日期**: 2026-07-12
**状态**: ✅ 已落地

按你的要求, 嵌入维度由所选模型决定, 不在代码里写死. `EmbeddingConfig.dimensions` 可为 `None`（用模型默认）或显式指定. ChromaDB collection 在首次 add 时由向量维度自动确定.

切换嵌入模型时需重新生成全量向量（已在 `llm-service.md` 文档化）.

---

## 决策 4: ReAct 实现风格

**日期**: 2026-07-12
**状态**: ✅ 已落地

采用**手写 ToolNode + 模型 function_call** 方案, 不用 LangGraph 的 `create_react_agent` 预构建.

理由:
- 便于在循环中加调试日志（Think-Act-Observe 每轮打印, 演示用）
- 灵活控制 max_iterations 和终止条件
- 能在 `state.errors` 中收集错误而非直接抛出

---

## 决策 5: 旧代码的处理

**日期**: 2026-07-12
**状态**: ✅ 已清理 (2026-07-14)

### 背景

v0.2 重构后, 曾一度存在两套并行代码:
- 旧: `src/accounts/` (auth + api_key), `src/modules/` (context/extraction/forward/memory), `src/storage/llm_service_store.py`, `src/models/llm_service.py`
- 新: `src/core/` + `src/infra/` + `src/persistence/` + `src/tools/`

CLI 与 API 已全部改用新结构, 旧目录处于 dead code 状态。这一割裂曾直接导致 bug: CLI 用的新版 `SqliteApiKeyStore` 忘了持久化 `key_full` 列, 导致 `show-key` 无法显示完整 key (旧版 `src/accounts/api_key_store.py` 反倒有正确实现, 却无人 import)。

### 决策

2026-07-14 一次性移除以下死代码:
- `src/accounts/`
- `src/modules/`
- `src/storage/`
- `src/models/`

同时清理若干 `__init__.py` 中未被使用的 re-export (`src/api/routes/__init__.py` / `src/api/schemas/__init__.py` / `src/persistence/__init__.py` / `src/infra/llm_service/__init__.py`) — 所有导入统一改为从子模块直取, 避免再次出现"两份实现互相错位"。

`src/persistence/api_key_store.py` 补齐 `key_full` 列与 `ALTER TABLE` 兼容语句, 修复上述 CLI bug。

---

## 决策 6: 提示词从常量迁到两层文件系统

**日期**: 2026-07-16
**状态**: ✅ 已落地 (v0.2.1)

### 背景

Agent 提示词此前是 `src/core/agents/prompts/*.py` 里的 Python 字符串常量, 高级用户想微调提示词必须改代码+重启。有明确需求把提示词开放给运维/后期面板编辑。

### 决策

1. **两层存储**:
   - 默认层: `src/core/agents/prompts/defaults/*.md`, 随包发布, 进 git, 运行时**永不修改**
   - 覆盖层: `data/prompts/*.md` (可配置, 见 `[storage] prompts_override_dir`), 用户可写, gitignore, 优先级高
2. **无缓存**: `PromptStore.load()` 每次读盘。文件 <10KB, IO 忽略不计, 换取"CLI 改文件立即生效, 不用 restart"。多进程/多机部署 (未来) 也自动一致。
3. **备份**: `data/prompts/.history/<name>-<YYYYMMDD-HHMMSS-NNN>.md`, `NNN` 处理同秒冲突, 每 name 保留最近 10 份 (超出按 mtime 淘汰)。数字选 10 是"够回溯若干次误操作, 又不至于把 .history 堆到失控"的折中。
4. **失败模式**: save 校验失败抛 `ValueError` 拒绝写盘; load 覆盖文件不存在 → 静默回退默认; load 覆盖文件 YAML frontmatter 损坏 → warn 日志 + 回退默认。
5. **Registry 白名单**: `PROMPT_REGISTRY` (8 项) 是 name → PromptSpec 的唯一权威, HTTP path 参数与 CLI 参数**必须**先过这一关才进入文件系统。防路径穿越 (`../etc/passwd` 类 name)。
6. **占位符统一约定**: 全部用 `__NAME__` (前后双下划线) + `str.replace`, 不用 `str.format`。原因: prompt 文本里含字面 JSON 花括号, `.format()` 遇到 `{"..."}` 会抛 `KeyError`; 且 `.format` 对未知占位符**静默通过**, 曾在 [factory.py:314](../src/core/agents/factory.py#L314) 让 `PROXY_THINKING_PROMPT.format(...)` 传给 `__X__` 模板时**静默返回未渲染的模板**, 上游模型看到字面 `__USER_NAME__` / `__MEMORIES__` — 见 [modules/agents.md §5.5 历史教训](modules/agents.md#55-提示词构建)。
7. **面板接口前置到位**: `/panel/admin/prompts/*` 6 条 REST 路由随此 PR 一并上线, 供未来 WebUI 直接调用, 不留"CLI 存在但面板还没接"的过渡期。

### 一并做的两件安全前置

- **admin 全路由鉴权**: 引入 `prompt` 写接口时, 把 admin router 现有的 health/logs/memories/relationship 一并加 `Depends(get_current_user)` (原本裸奔)。避免只保护新接口而遗留旧漏洞。
- **sentence_classifier 迁移**: 该工具原本用 `{text}` + `.format` 与全项目约定不一致, 一并改成 `__TEXT__` + `.replace` 纳入 PromptStore。

### 显式范围外

- **乐观锁**: v0.2.x 单管理员单进程, 不加 If-Match / version 冲突检测, frontmatter `version` 字段仅供日志/回滚参考。
- **A/B 或热切换**: 只有单一覆盖, 无 profile 概念。
- **LLM 自改提示词** (人格自我演化): 长期目标, 本次不涉及。

---

## 嵌入模型单绑定 + Reindex + Prune (v0.2.4)

**日期**: 2026-07-17

### 背景

v0.2.3 把 `main / assist / embedding / rerank` 全部纳入 `role_bindings` 优先级列表, 上游出错自动 fallback 到下一位。这套模型对生成侧 (main/assist/rerank) 合理, 但对嵌入侧有一个根本错误:

- 不同嵌入模型输出的向量维度不同; 即便维度相同 (2048 vs 2048), **语义空间也完全不同** — 换模型不能靠 fallback 蒙混, 已存向量会瞬间失去意义
- ChromaDB collection 首次写入时锁定维度; 中途换模型要么维度不匹配直接 crash (较好), 要么维度巧合一致但检索质量默默劣化 (更糟)
- v0.2.3 的 [`lifecycle.py`](../src/core/memory/lifecycle.py) 与 [`tools/vector_search.py`](../src/core/tools/vector_search.py) 里的 `forwarder.embed(x)` 会走 MultiForwarder 的多候选遍历 — 嵌入语境下这就是灾难

同时用户提出两个衍生需求:

1. **模型规格可见性**: `/models` 面板卡片只展示 `service_id/model`, 判断"这个 8B 上下文够不够"、"这个嵌入维度对不对"完全靠用户记忆。竞品 (AstrBot / One-API / LiteLLM) 都有 `context_length` / `dim` 字段。
2. **大规模衰减清理**: 记忆到万级后, 已过期日程 + 已衰减到 FORGOTTEN 阈值以下的低价值记忆积累会拖慢重建。`MemoryEntry` 已有 `decay_rate` / `priority` / `expires_at` / `is_forgotten` / `memory_type`, 纯本地就能判定, 不需 LLM。

### 决策

1. **嵌入 = 单绑定**。add 第二条嵌入返回 409; reorder 对嵌入返回 400。Store 层 [`add_role_binding`](../src/infra/llm_service/store.py) 前置校验。
2. **主/辅助/重排保持多候选** fallback。重排换模型只影响精排, 不动向量库。
3. **ChromaDB collection 锁定 `(service_id, model, dim)` 元数据**。首次写入时 [`vector_store.lock_embedding()`](../src/infra/vector_store.py) 落到 collection.metadata; 之后每次写入前 assert 一致, 不一致抛 `VectorStoreLockError`。查询侧不校验 (读旁路)。
4. **换嵌入模型必须走 reindex**。UI 用替换对话框弹二次确认 + 记忆数警告; 用户可选"替换并重建"或"仅替换 (稍后手动重建)"。
5. **Reindex 端点异步**, `ReindexProgress` 是**进程内单例**, 崩溃就复位; 前端轮询 `GET /memory/reindex/status`。刻意不做持久化 — 进度值有意义的只是"当前跑没跑完", 重启就得重跑。
6. **Reindex + Prune 同任务两模式**。reindex 遍历时可 opt-in 顺便清理; prune 也是独立端点, 面板/CLI 直触。逻辑同源: [`should_prune()`](../src/core/memory/reindex.py) 纯函数。
7. **PERMANENT 记忆绝对不动**, 无 opt-out。想删单条走既有 `/memories/{id}` DELETE。理由: 用户明确声明"永久"就是永久, 唯一显式覆盖点是单条 API。
8. **Reindex 期间拒写**: `progress.state == 'running'` 时 `MemoryLifecycle.store_candidate` 早退并 log 警告。避免边重建边写入导致语义混杂。单用户系统可接受, 多用户/在线迁移不在本轮范围。
9. **元数据字段可选**。`context_length` 纯面板展示, 不消费; `embedding_dim` 会被 forwarder 消费, 作为 `dimensions=` 传给上游 (DashScope v3 等可变维模型的必要参数)。
10. **维度探测按钮**: Add 对话框加"测试维度", POST `/model-bindings/probe-dimension` 临时 `embed("hi")` 一次读长度, **不落库**。用户可采纳也可手填。

### 一并做的三件

- **删除嵌入绑定** 走同一个 delete 路由, 但 UI 提示语强化: "删除后须重新添加嵌入模型才能写入新记忆, 且已存向量将无法与新模型对齐"
- **清理 dry-run**: prune 端点接受 `dry_run=true`, 只返回 breakdown (forgotten/expired/low_priority 各多少) 不删。让用户看清楚阈值把什么类型带走了再点确认
- **CLI 走 HTTP**: `memory reindex/prune` 子命令通过 panel HTTP 调用, 而不是在 CLI 进程里 in-process 跑。避免与运行中服务器争夺 Chroma 单例; 也让"CLI 是远程 SSH 客户端"的场景直接可用

### 显式范围外

- **在线迁移** (换模型期间不停写): 单用户系统接受"重建期间拒写"的简单方案。多用户需引入双写 / 影子索引
- **PERMANENT 也可清理**: 不做例外, 单条走 API DELETE
- **prune 备份 / 导出**: 未来若需要"先导出 JSONL 备份再删"再加
- **静态 model spec 表** (LiteLLM `model_prices.json` 那种): 不引入, `context_length` 让用户手填
- **rerank 需 reindex**: 不需要, 换重排模型只影响精排质量, 向量库不动
- **进度持久化**: 只在内存; 进程崩溃后需重触发, 可接受
- **CLI 独立进度显示**: `memory reindex` 阻塞直到完成, 简单打印百分比; 不做异步 poll

---

## 调试面板 + HTTP hop 观测 (v0.2.5)

**状态**: ✅ 已落地 (v0.2.5)

### 背景

前后端联调时排查"客户端 → Mnemosync → 上游"这条链路的问题, 只能翻 `data/http_logs.db` + 上游服务商控制台交叉比对; 特别是流式请求 (`stream=true`) 想看 upstream 到底吐了什么内容非常麻烦。需要一个可视化面板, 把每一跳的请求 / 响应 / body / 流帧全部拍在一个界面上, 按 correlation_id 分组。

### 设计约束

- **不落盘**: 调试信息包含明文 body 与解密后的 API Key note, 是调试临时状态; 全部走内存 ring buffer (500 条), 崩溃即丢。生产上如需持久证据仍走 `http_logs.db`。
- **零成本兜底**: 面板未打开时 emit 必须近似 no-op, 不给主链路加延迟。方案: 惰性 gate — 订阅数为 0 时 `emit()` 立刻返回, 完全跳过 body 塑形。
- **本机自动 API Key**: 面板要能真的调 `/v1/chat/completions`, 需要一个 Bearer 凭据; 用户不该被要求手动"为面板申请一个 key"。方案: 面板挂载时 `POST /panel/admin/debug/session-key` 自动生成或复用一个 `source='panel-debug'` 的 key。
- **不污染用户 key 视图**: `api_keys` 表加 `source` 列 (`user` / `panel-debug`); `/panel/api-keys` GET / DELETE 只认 `source=user`。panel-debug key 只能通过面板生命周期自动清理, 不能被人手撤。
- **面板关闭要能感知**: 用户直接关标签页无法保证前端 hook 触发。改为服务器端观察 SSE 订阅数 — 从 1 掉到 0 后启动 30 秒 grace timer, 超时 → 删除所有 `source=panel-debug` 的 key。startup / shutdown 时也各清一次孤儿。
- **路由切换不断线**: 前端 SSE 订阅放在 Pinia store 单例, 由 `DebugChatPage.vue` 挂载时 `activate()`, 卸载时 `deactivate()`。用户在面板内跳转其他管理页会中断订阅, 但 30s grace 缓冲期内回到面板即恢复, 不会误清 key。

### 关键模块

| 位置 | 作用 |
|------|------|
| [`src/persistence/api_key_store.py`](../src/persistence/api_key_store.py) `source` 列 | 区分用户创建 vs 面板自动; `list_all(source=...)` + `delete_by_source(source)` |
| [`src/infra/debug_bus.py`](../src/infra/debug_bus.py) `DebugEventBus` | 500 条 ring buffer + `asyncio.Queue` 每订阅者 + 惰性 emit gate + grace-timer 清理回调 |
| [`src/infra/debug_context.py`](../src/infra/debug_context.py) `use_agent(name)` + correlation_id | `ContextVar` 沿 async 调用链传递, 让每个 upstream hop 都能打上"来自哪个 agent"标签 |
| [`src/infra/forwarder/debug_hook.py`](../src/infra/forwarder/debug_hook.py) `set_debug_bus` | 模块级单例注入, 打破 forwarder 依赖 FastAPI `app.state` 造成的循环 |
| [`src/api/middleware.py`](../src/api/middleware.py) | 每个 inbound 请求打 correlation_id + 查 API Key note; emit `inbound_request` / `inbound_response` |
| [`src/api/routes/admin_debug.py`](../src/api/routes/admin_debug.py) | REST + SSE 端点 |

### Agent 标签点

- `run_main_dialogue` / `run_memory_analysis` / `run_relationship_analysis` / `run_prompt_cleaning` / `run_proxy_thinking` 各在 [`factory.py`](../src/core/agents/factory.py) 用 `with use_agent(...)` 包住
- 流式主对话在 [`forward.py`](../src/api/routes/forward.py) 用 `use_agent("main_dialogue_stream")`
- 向量检索 [`vector_search.py`](../src/tools/vector_search.py) 用 `use_agent("memory_retriever")`
- 记忆入库 [`lifecycle.py`](../src/core/memory/lifecycle.py) 用 `use_agent("memory_lifecycle")`

### 显式范围外

- **持久化调试事件**: 不做; 有需要走 http_logs
- **面板级过滤 / 搜索**: 前端只做 correlation_id 分组 + 展开; 全文过滤未来再加
- **beforeunload 触发式清理**: 曾考虑, 但 tab 强关时不可靠; 改用 SSE 订阅数 + grace 的服务端观察
- **跨进程共享**: bus 是当前进程单例, 崩溃后丢弃; mnemosync 单用户单机, 可接受

---

## 跨前端短期记忆 (v0.2.6)

### 背景

v0.2.5 之前, 每次 `/v1/chat/completions` 用的都是**客户端传来的** `messages`; 换 AstrBot → AIRI, 上下文立刻断; 前端点"清空对话"后模型也一并失忆。这与 Mnemosync 的核心承诺 — **跨前端统一人格记忆** — 直接矛盾。

问题的根源不是"客户端没传对", 而是"不能依赖客户端传对": 有的客户端每轮只传当前一句 (AstrBot 群聊场景), 有的每轮传完整历史, 有的用户主动清空。**中间件不能修改客户端行为**, 只能自己维护真相。

### 决策

1. **服务器持有真相**: 新增 append-only `conversation_turns` 表 (id, role, content, ts, token_count, source_frontend)。所有前端写入同一 bucket, 无 thread/user 分区 — 单人格单用户定位 (见 `mnemosync-single-persona-scope`) 下"多前端 = 同一用户", 分区就违背语义。
2. **忽略客户端历史**: `forward.py` 只从 `messages` 里挑**最后一条 user**, 服务器 history 全部来自 `conversation_turns`。客户端的 system 消息仍走 `prompt_cleaning`, 但不再影响上下文长度。
3. **双窗口装填**:
   - 时间窗 `settings.storage.short_term_days` (默认 7d) 硬边界
   - 模型窗用 `ResolvedCandidate.context_length` 算预算 `= ctx - system - new_user - reserve_output`, 从最老那端裁剪
   - reserve_output 优先客户端 `max_tokens`; 否则 `min(4096, ctx/4)` 下限 512
4. **Token 估算走保守启发式** `len(text) // 2 + 8`。不接入真实 tokenizer — 换模型时 tokenizer 形变太大, 中间件维护成本高; 用估算 + 保留区兜底就够避免上游 4001。
5. **source_frontend = api_key.note** (服务器派生, 非客户端 header)。仅作观测, 不参与查询条件。
6. **后台清理**: `lifespan` 起 24h loop, `delete_before(now - N 天)`。
7. **面板重置端点**: `GET/DELETE /panel/admin/conversation-turns`; 空 `since` 全清, 带 `since` 只清早于该时间的。前端 UI 的清空按钮不动服务器 — 服务器的连续记忆只有面板能抹。

### 显式范围外

- **thread/user 分区**: 单人格单用户阶段不做; 未来上多用户时按 `source_user` 分表
  > *(v0.3.0 注: 多用户已落地, 但未分表 — 改为 `source_user` 存 effective_user_id 做逻辑隔离 + conversation_turns 按 space_id 分区, 见上文 v0.3.0 决策)*
- **真实 tokenizer**: 不引入 tiktoken 等; 估算 + 保留区已够用
- **回滚客户端 UI 语义**: 用户在客户端"清空对话"依然会让客户端自己丢历史, Mnemosync 不管
- **assistant turn 精确 token 计数**: 用同一 estimate 口径; 上游返回的 `usage.completion_tokens` 更准但读起来要跨流处理, 收益不值

---

## 关系称呼动态演化 (v0.2.10)

### 背景

v0.2.9 把 `[persona.relation]` 抽到 TOML, 记忆分析 / 关系分析 prompt 通过占位符消费三个字段。但那是**安装态基线**, 一旦启动就冻结; 用户在对话里说"以后叫我小哥"或关系从"兄妹"漂移到"恋人", 无处落库。下一轮 prompt 里模型依旧被灌旧称呼, Agent 看得见信号却无法演化 — 缺 tool + 缺存储。

### 决策

1. **持久化选表, 不选 JSON blob**: `relationships` 加 3 个 `TEXT NULL` 列 (`persona_addressing / user_addressing / context`), NULL 语义 = "沿用上层基线"。运行时 `nodes.py._resolve_addressing()` 按 **表 → `persona_override.toml` → `config.local.toml` → 资源默认** 四层取值, 面板 `GET /panel/admin/relationship` 直接返回**当前有效值**而非 NULL。
2. **审计日志独立表 `relationship_audit_log`**: 一次多字段更新写多行 (字段级), 便于按字段回退。source 只有 `agent` / `manual` 两种。选独立表而非 JSON diff column 是因为审计需要按字段查询。
3. **Tool 极简**: `update_addressing(persona_addressing?, user_addressing?, context?, reason)` — 三字段全部 nullable, `reason` ≥ 10 字必填。`persona_id / user_id` 通过 factory 闭包 bind, Agent 看不见, 防跨用户改写。
4. **判断阈值不写死**: 不设"K 轮无撤回"这种数字规则。Prompt 给 Agent 提"判断维度" (是否玩笑 / 场景扮演 / 引用他人 / 撤回信号), 让语用判断决定; 代码层只兜底 `reason` 长度 + 至少一字段非 None。
5. **信号只看用户侧**: 强调"不能因为我上一轮回复用了新称呼就以为已稳定 — 模型自己的输出是 prompt 回声"。
6. **兜底面板 override**: `PUT /panel/admin/relationship` 允许人工写 (source=manual); UI 加"编辑称呼"对话框 + 变更历史面板, 每条 audit 可"回退到此"触发一次反向 PUT。
7. **memory_analysis Agent 不给此 tool**: 保持"事实提取"纯净, 关系演化只由 relationship_analysis 负责。memory_analysis 仍消费动态称呼 (通过 `_resolve_addressing`), 但不写。

### 显式范围外

- **K 轮撤回窗口 / 数字阈值**: 交给 Agent 判断
- **addressing 的 embedding / 相似度检索**: 纯字符串, 用户说什么就落什么
- **锁定称呼字段**: 靠"手动 PUT 覆写 + audit 回退"兜底, 加锁是提前优化
- **动 `default.toml`**: TOML 仍是"新装基线", `POST /panel/admin/persona/reset` 会清空 `relationships` 让新对话回落基线

---

## 人格面板编辑 (v0.2.11)

### 背景

v0.2.9 的 `[persona]` 段只能改 `config.local.toml` 后重启, 面板无法编辑; 而 `POST /panel/admin/persona/reset` 又只能"清空业务数据回落 TOML", 缺一个正向编辑入口。用户希望在面板直接改人格 name / prompt / relation 三段并热重载。

### 决策

1. **文件覆盖层 `data/persona_override.toml`**: 面板 `PUT /panel/admin/persona` 全量写此文件, 优先级 `override > config.local > 资源默认` (`load_settings` 三级合并)。选文件而非表因为人格是"启动态 + 面板偶尔改", 不需要事务或字段级审计, 文件更好 diff。
2. **格式机械生成 + 不要手编**: 每次 PUT 全量覆写, 转义策略固定 (basic string + triple-quoted prompt), 不解析注释。手编的注释和格式会被下次 PUT 覆盖 → 文档明确警告。
3. **热重载**: PUT 完成后 `_reset_settings()` 清缓存, 下次 `get_settings()` 重新加载。运行中的图节点若已捕获旧 settings 需等下一轮请求 — 单人格单机场景可接受。
4. **重置分层**: `DELETE /panel/admin/persona` 删本文件 → 回落到 `config.local.toml [persona]` 或资源默认; 与 `POST /panel/admin/persona/reset` (清 memory / relationships / conversation_turns) 语义严格分离, **不混用**。
5. **GET 显示 overridden 标志**: `overridden: bool = data/persona_override.toml.exists()`, 前端据此渲染"已覆盖 / 重置为默认"按钮态。

### 显式范围外

- **多 persona / 每用户 persona 切换**: 单人格单用户阶段不做
- **prompt 版本历史 / diff**: 走 PromptStore 那套 (面板 Agent prompt 覆盖) 而非 persona override; 需要时再扩
- **partial PUT**: 一次全量覆写更简单, 前端在 GET 时拿到当前值填表单即可

---

## 单人格多用户基础 (v0.3.0)

### 背景

v0.2.x 单人格单用户: `source_user` 恒为 `"default"`, 所有记忆/关系/对话共享一桶。群聊场景 (AstrBot QQ 群) 要求识别不同参与者、按人隔离记忆、群聊上下文不串台; 且客户端是不可控黑盒 ("你永远无法要求前台为你适配"), 身份必须在服务器侧解决。当前实现见 [modules/identity.md](modules/identity.md), 无实际部署者, 决定**不兼容 v0.2.x 的 `"default"` 用户**, 直接移除全部硬编码。

### 决策

1. **身份三层模型**: Actor (一个平台账号, `(frontend, external_key)` 唯一, 系统按策略自动建档) → UserGroup (一个真实人, 管理员手动绑定) → effective_user_id (绑组取 group_id, 否则 actor_id) 作为记忆与关系的**唯一隔离边界**。`memory_entries.source_user` 与 `relationships.user_id` 语义升级为 effective_user_id, 无 schema 变更。
2. **策略绑定 API Key**: 一个 Key = 一个前台接入, 绑定一个身份策略 (direct / api_key_bound / regex / llm)。身份从请求内容/字段**服务器侧提取**, 客户端不声明、不可伪造。regex 策略面向 AstrBot 式"身份信息塞 prompt 文本"的现实; llm 策略兜底不规则格式。
3. **非归属模式**: 无策略或解析失败 → `effective_user_id = None`: 不建 Actor、不读写私有记忆、仅 PUBLIC 记忆可见、照常回复。宁可不记忆也不串户——取代 v0.2.x 的 `"default"` 兜底。
4. **空间事件流**: `conversation_turns` 按 `space_id` 分区, 提交时同事务分配空间内单调序号 `committed_sequence` (MAX+1), 乱序到达标记 `late_arrival`。群聊装填只读本空间 (`list_for_space`)——群 A 的对话绝不泄入群 B 或私聊; 私聊/非归属轮次不分区。
5. **幂等重放**: 平台重发按 `(api_key.id, external_event_id)` 命中缓存, 原样返回首次响应 (流式拼 SSE), **零 LLM 调用、零记忆副作用**。失败不写缓存 (允许重试再生成), `INSERT OR IGNORE` 保留首次结果。独立 `data/idempotency.db` 避免与热库 WAL 互扰。
6. **受众过滤两级**: 粗筛走 ChromaDB `$or` where (自己桶 / PUBLIC / 本空间, 超集), 精筛走 `AudienceFilter.is_visible` (关系门槛、deny/allow 策略只能在 Python 层判)。SOURCE_RESTRICTED 非来源用户永不可见——即使同空间。记忆先过滤再交给模型, 不靠 prompt 防泄露。
7. **关系属于"人"不属于账号**: 称呼/关系写 effective_user_id; `update_addressing` 闭包附带 `actor_id` 仅溯源。管理端点支持传 `actor_id` 自动解析——面板上点任一平台账号都查到同一个人的关系。
8. **identity.db 独立库**: 身份四表自成 `data/identity.db`, 与 memory/conversation 分离 (读写模式不同, WAL/vacuum 互不干扰), 与幂等库同理。

### 显式范围外

- **多人格**: `persona_id` 从 state 读取 (值仍固定 `"default"`), personas 表与多人格路由不做
- **SpaceState / Checkpoint / 群聊摘要**: 空间只做事件流与隔离, 不做状态机
- **跨平台身份自动绑定**: 绑组由管理员手动操作, 不做自动推断
- **v0.2.x `"default"` 用户数据迁移**: 无实际部署者, 不迁移

### 同期落地 (Phase 1 收尾, v0.2.12)

- 提示词清洗改单次 LLM 重写 (`sentence_classifier` 工具移除)
- 衰减改确定性公式 `run_deterministic_decay()` (`time_decay_calculator` 工具移除; `decay_evaluations` 字段保留但恒空)
- 情绪分析去重: `main_dialogue_node` 预计算一次, 经 state 共享给两个分析 Agent (不再各调一次)
- memory_analysis 迭代上限 6→4, relationship_analysis 3→2

---

## 待补充

后续遇到的新决策会追加到本文档.
