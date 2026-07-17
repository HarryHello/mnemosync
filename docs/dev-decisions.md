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

## 待补充

后续遇到的新决策会追加到本文档.
