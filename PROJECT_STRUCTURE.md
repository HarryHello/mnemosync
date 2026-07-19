# Mnemosync 项目目录结构

以下反映当前 (v0.2.6) 代码仓库的实际目录结构。

```
mnemosync/
├── LICENSE
├── README.md
├── PROJECT_STRUCTURE.md            # 本文件
├── Dockerfile
├── docker-compose.yml
├── install.sh                      # 单行部署脚本 (克隆到 ~/.mnemosync)
├── config.example.toml             # 配置模板 (含 [persona] / [storage] / [graph] / [runtime])
├── config.local.toml               # 本地配置 (git 忽略, 可缺失走全默认)
├── pyproject.toml                  # Python 项目元数据 + 依赖 (uv/hatch)
├── uv.lock
├── pyrightconfig.json
│
├── docs/                           # 文档
│   ├── architecture.md
│   ├── auth.md
│   ├── configuration.md
│   ├── deployment.md
│   ├── dev-decisions.md
│   └── modules/
│       ├── agents.md
│       ├── api-key.md
│       ├── cli.md
│       ├── forward.md
│       ├── langgraph.md
│       ├── llm-service.md
│       ├── memory-system.md
│       ├── message-extraction.md
│       ├── message-processing.md
│       └── tools.md
│
├── data/                           # 运行时数据 (git 忽略)
│   ├── api_keys.db                 # API Key SQLite 库 (含 source 列: user | panel-debug)
│   ├── auth.db                     # 用户认证 (账号 + Session)
│   ├── memory.db                   # 长期记忆元数据
│   ├── conversation.db             # v0.2.6: 跨前端对话流水 (conversation_turns)
│   ├── llm_service.db              # LLM 服务商 + role_bindings
│   ├── http_logs.db                # HTTP 请求日志 (--debug)
│   ├── chroma/                     # ChromaDB 向量索引
│   └── prompts/                    # v0.2.1: Agent 提示词用户覆盖层
│       └── .history/               # 每个 name 保留最近 10 份备份
│
├── scripts/                        # 开发脚本 (探针 / 连通性测试)
│   ├── probe_function_call.py
│   ├── test_agents.py
│   └── test_connectivity.py
│
├── ui/                             # 前端 Vue 面板 (独立子项目, 见 ui/README.md)
│
└── src/                            # 源代码
    ├── __init__.py
    ├── main.py                     # 入口: `python -m src.main` 与 uvicorn app
    │
    ├── cli/                        # 命令行入口
    │   ├── cli.py                  # `mnemosync` 顶层命令 (serve / init / ...)
    │   ├── cli_interactive.py      # `mnemosync login` 交互式 shell
    │   ├── ask.py                  # `ask` 调试命令 (直连 LangGraph)
    │   └── prompt_cmd.py           # `prompt` 提示词覆盖子命令 (list/show/set/reset/validate)
    │
    ├── api/                        # HTTP API 层 (FastAPI)
    │   ├── __init__.py             # 组装 /panel 前缀的 api_router + 顶层 forward_router
    │   ├── lifespan.py             # 应用启动/关闭: 连库 + 后台清理任务 (含 conversation prune loop)
    │   ├── deps.py                 # FastAPI 依赖注入 (从 app.state 取 store 单例)
    │   ├── middleware.py           # HTTP 日志中间件 (含 debug_bus emit)
    │   ├── reasoning_control.py    # 代理推理决策 (should_use_proxy_thinking)
    │   ├── routes/
    │   │   ├── admin.py            # /panel/admin/* (Depends(get_current_user))
    │   │   │                       #   prompts / model-bindings / memories / relationship /
    │   │   │                       #   memory reindex+prune / conversation-turns
    │   │   ├── admin_debug.py      # v0.2.5: /panel/admin/debug/* (session-key / events / stream)
    │   │   ├── api_key.py          # /panel/api-keys/* (source=user 才列出)
    │   │   ├── auth.py             # /panel/auth/*
    │   │   └── forward.py          # /v1/chat/completions (OpenAI 兼容)
    │   └── schemas/                # Pydantic 请求/响应模型
    │       ├── admin.py            # Prompt* / RoleBinding* / Reindex* / Prune* / DebugEvent* / ConversationTurn*
    │       ├── api_key.py
    │       ├── auth.py
    │       └── forward.py
    │
    ├── core/                       # 核心业务层
    │   ├── config.py               # 配置加载: Settings + PersonaConfig + StorageConfig + ...
    │   ├── config_writer.py        # 运行期回写 config.local.toml (供 CLI 用)
    │   ├── prompts/                # 提示词两层存储 (defaults + 用户覆盖)
    │   │   ├── registry.py         # PROMPT_REGISTRY (PromptSpec 白名单)
    │   │   └── store.py            # PromptStore (无缓存读盘 + 校验 + 备份)
    │   ├── agents/                 # LangGraph Agent
    │   │   ├── base.py             # ReAct 循环 / simple completion
    │   │   ├── factory.py          # run_main_dialogue / run_memory_analysis / ...
    │   │   └── prompts/            # 各 Agent 的 prompt builder (走 PromptStore)
    │   │       ├── defaults/       # 默认提示词 Markdown (随包发布)
    │   │       │   ├── main_dialogue_frame.md
    │   │       │   ├── memory_analysis.md
    │   │       │   ├── memory_analysis_decay_header.md
    │   │       │   ├── relationship_analysis.md
    │   │       │   ├── prompt_cleaning_system.md
    │   │       │   ├── prompt_cleaning_user.md
    │   │       │   ├── proxy_thinking.md
    │   │       │   └── sentence_classifier.md
    │   │       ├── memory_analysis.py
    │   │       ├── prompt_cleaning.py
    │   │       ├── proxy_thinking.py
    │   │       └── relationship_analysis.py
    │   ├── graph/                  # LangGraph 编排
    │   │   ├── builder.py          # build_graph()
    │   │   ├── nodes.py            # 各节点实现
    │   │   └── state.py            # AgentState TypedDict
    │   ├── models/                 # v0.2.3: 角色 → 候选解析
    │   │   └── resolver.py         # RoleResolver (从 role_bindings + services 组合 ResolvedCandidate)
    │   └── memory/                 # 记忆领域模型
    │       ├── context.py          # render_main_dialogue_system + build_main_dialogue_messages
    │       ├── lifecycle.py        # 衰减/召回策略 (MemoryLifecycle)
    │       ├── models.py           # MemoryEntry / MemoryType / DecayState
    │       ├── reindex.py          # v0.2.4: Reindexer + ReindexProgress + should_prune
    │       └── short_term.py       # v0.2.6: build_short_term_history + trim_by_budget + estimate_tokens
    │
    ├── infra/                      # 基础设施层
    │   ├── extraction.py           # 从 OpenAI 消息数组提取用户轮次 (仅供后台记忆图, 主对话不再依赖)
    │   ├── debug_bus.py            # v0.2.5: DebugEventBus (subscriber_count + grace 清理)
    │   ├── debug_context.py        # v0.2.5: contextvars 传播 correlation_id / use_agent 标签
    │   ├── forwarder/              # 上游 LLM 请求转发
    │   │   ├── connection_pool.py
    │   │   ├── forwarder.py        # Forwarder (单服务商) + UpstreamError/UpstreamTimeout
    │   │   ├── multi.py            # MultiForwarder (多候选 + fallback, embed 已改单绑定不 fallback)
    │   │   └── debug_hook.py       # v0.2.5: hook 把出/入方向写进 debug_bus
    │   ├── llm_service/            # LLM 服务商 + role_bindings
    │   │   ├── models.py           # LLMServiceProvider / ModelConfiguration / RoleBinding / ResolvedCandidate
    │   │   └── store.py            # SQLite 存储 (services / role_bindings 两表)
    │   └── vector_store.py         # ChromaDB 封装 (含 v0.2.4 embedding lock metadata)
    │
    ├── persistence/                # 持久化层 (SQLite)
    │   ├── api_key_store.py        # SqliteApiKeyStore (含 source 列: user | panel-debug)
    │   ├── auth_store.py           # SqliteAuthStore (用户 + Session)
    │   ├── conversation_store.py   # v0.2.6: SqliteConversationStore (append-only turns)
    │   ├── http_log_store.py       # v0.2.5: HttpLogStore (--debug 模式落库)
    │   └── memory_store.py         # SqliteMemoryStore
    │
    └── tools/                      # 供 Agent 调用的工具函数
        ├── emotion_analyzer.py
        ├── sentence_classifier.py  # 单句分类 (提示词清洗 Agent 用)
        ├── time_decay_calculator.py
        └── vector_search.py
```

---

## 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| CLI | `src/cli/` | 命令行入口 (serve / login / 交互 shell / prompt 覆盖) |
| API | `src/api/` | HTTP 路由 (/v1 对外 + /panel 面板)、鉴权、lifespan、debug |
| Core | `src/core/` | 业务逻辑: 配置、Agent、LangGraph 编排、记忆领域模型、角色→候选解析、提示词两层存储 |
| Infra | `src/infra/` | 与外部系统交互: 上游转发 (多候选)、LLM 服务商元数据、向量库、消息提取、debug 事件总线 |
| Persistence | `src/persistence/` | SQLite 存储实现 (auth / api_key / memory / conversation / http_log) |
| Tools | `src/tools/` | 供 Agent 通过工具调用触发的功能 |

## 命名与导入约定

- 目录 / 模块使用 snake_case。
- 尽量**直接从子模块导入** (`from src.persistence.api_key_store import ...`)，不要依赖包 `__init__` 的 re-export。
- 保留 re-export 的包: `src.api` (对外挂载 router) 与 `src.core.agents` / `src.core.memory` / `src.infra` / `src.core.agents.prompts` (供 factory 与 graph 内部聚合)。

## 数据目录

`data/` 下的 SQLite 与 ChromaDB 由 `python -m src.main init-internal` 初始化；结构由各 `*Store.init_db()` 或 lifespan 阶段的 `connect()` 负责，无独立 migration 目录 (schema 有增列时走 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 幂等升级)。
