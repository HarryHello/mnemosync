# Mnemosync 项目目录结构

以下反映当前 (v0.3.4) 代码仓库的实际目录结构。

```
mnemosync/
├── LICENSE
├── README.md
├── PROJECT_STRUCTURE.md            # 本文件
├── AGENTS.md                       # AI Agent 指南
├── Dockerfile
├── docker-compose.yml
├── install.sh                      # 单行部署脚本 (克隆到 ~/.mnemosync)
├── uninstall.sh                    # 卸载脚本
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
│   ├── troubleshooting.md
│   ├── modules/
│   │   ├── agents.md               # 多 Agent 设计 (6 个 Agent)
│   │   ├── api-key.md              # API Key 管理
│   │   ├── cli.md                  # CLI 命令参考
│   │   ├── forward.md              # 上游转发 (Forwarder / MultiForwarder)
│   │   ├── identity.md             # 身份管理 (v0.3.0)
│   │   ├── identity-plugin.md      # 身份解析插件 (v0.3.1)
│   │   ├── langgraph.md            # LangGraph 编排
│   │   ├── llm-service.md          # LLM 服务商 + role_bindings
│   │   ├── memory-system.md        # 记忆系统 (短期 + 长期)
│   │   ├── message-extraction.md   # 消息提取 (保留, 无主路径消费者)
│   │   ├── message-processing.md   # 消息处理全流程
│   │   ├── persona-definition.md   # 结构化人格定义 (v0.3.3) [新增]
│   │   ├── character-card.md       # 角色卡导入 (v0.3.3) [新增]
│   │   ├── internal-tools.md       # 内部工具与身份绑定 (v0.3.3) [新增]
│   │   └── tools.md                # Agent 工具 (vector_search / update_addressing)
│   ├── rfcs/
│   │   ├── agent-run-contract.md
│   │   └── structured-persona.md
│   └── research/
│       └── human-like-group-chat-research.md
│
├── data/                           # 运行时数据 (git 忽略)
│   ├── api_keys.db                 # API Key SQLite 库
│   ├── auth.db                     # 用户认证 (账号 + Session)
│   ├── memory.db                   # 长期记忆元数据 + 关系
│   ├── conversation.db             # 跨前端对话流水 (conversation_turns)
│   ├── llm_service.db              # LLM 服务商 + role_bindings
│   ├── http_logs.db                # HTTP 请求日志 (--debug)
│   ├── identity.db                 # 身份四表 (actors / user_groups / memberships / strategies)
│   ├── idempotency.db              # 幂等重放缓存
│   ├── notifications.db            # 通知中心
│   ├── persona.db                  # 结构化人格版本存储 (personas + persona_versions)
│   ├── lorebook.db                 # Lorebook 条目
│   ├── space_policy.db             # 空间社交策略
│   ├── chroma/                     # ChromaDB 向量索引
│   └── prompts/                    # Agent 提示词用户覆盖层
│       └── .history/               # 每个 name 保留最近 10 份备份
│
├── plugins/                        # 身份解析插件
│   ├── __init__.py
│   └── astrbot.py                  # AstrBot QQ 适配器 (群聊快照拆分)
│
├── scripts/                        # 开发脚本 (探针 / 连通性测试)
│   ├── probe_function_call.py
│   ├── test_agents.py
│   ├── test_connectivity.py
│   ├── seed_mock_memories.py
│   └── mock_notifications.py
│
├── ui/                             # 前端 Vue 面板 (独立子项目, 见 ui/README.md)
│
└── src/                            # 源代码
    ├── __init__.py
    ├── main.py                     # 入口: `python -m src.main` 与 uvicorn app
    │
    ├── cli/                        # 命令行入口
    │   ├── __init__.py
    │   ├── cli.py                  # `mnemosync` 顶层命令 (serve / init / ...)
    │   ├── cli_interactive.py      # `mnemosync login` 交互式 shell
    │   ├── ask.py                  # `ask` 调试命令 (直连 LangGraph)
    │   ├── prompt_cmd.py           # `prompt` 提示词覆盖子命令
    │   └── identity_cmd.py         # `identity` 身份管理子命令 (v0.3.0)
    │
    ├── api/                        # HTTP API 层 (FastAPI)
    │   ├── __init__.py             # 组装 /panel 前缀的 api_router + 顶层 forward_router
    │   ├── lifespan.py             # 应用启动/关闭: 连库 + 后台清理任务
    │   ├── deps.py                 # FastAPI 依赖注入
    │   ├── middleware.py           # HTTP 日志中间件 (含 debug_bus emit)
    │   ├── reasoning_control.py    # 代理推理决策 (should_use_proxy_thinking)
    │   ├── tool_policies.py        # 工具策略过滤 (白名单/黑名单/冷却)
    │   ├── tool_transactions.py    # 工具续轮尾部校验
    │   ├── state.py                # AppState 数据类
    │   ├── routes/
    │   │   ├── forward/            # /v1/chat/completions + /v1/models
    │   │   │   ├── __init__.py     # 主路由逻辑 + 身份解析 + 幂等
    │   │   │   ├── stream.py       # 流式处理
    │   │   │   ├── nonstream.py    # 非流式处理
    │   │   │   ├── identity.py     # 身份解析逻辑
    │   │   │   ├── idempotency.py  # 幂等重放
    │   │   │   ├── persistence.py  # 事件落库
    │   │   │   └── memory_graph.py # 异步记忆图
    │   │   ├── admin*.py           # 管理面板各模块路由
    │   │   ├── api_key.py          # API Key 管理
    │   │   └── auth.py             # 认证路由
    │   └── schemas/                # Pydantic 请求/响应模型
    │       ├── admin.py
    │       ├── api_key.py
    │       ├── auth.py
    │       └── forward.py
    │
    ├── core/                       # 核心业务层
    │   ├── config.py               # 配置加载 (Settings 单例, 三层优先级)
    │   ├── config_writer.py        # 运行期回写 config.local.toml
    │   ├── constants.py            # 全局常量
    │   ├── prompts/                # 提示词两层存储 (defaults + 用户覆盖)
    │   │   ├── registry.py         # PROMPT_REGISTRY (PromptSpec 白名单)
    │   │   └── store.py            # PromptStore (无缓存读盘 + 校验 + 备份)
    │   ├── agents/                 # LangGraph Agent 执行函数
    │   │   ├── base.py             # ReAct 循环 / simple completion
    │   │   ├── factory.py          # Agent 工厂 (main/memory/relationship/expressor...)
    │   │   └── prompts/            # Prompt builder
    │   │       ├── defaults/       # 默认提示词 Markdown (随包发布)
    │   │       └── *.py            # builder 函数
    │   ├── graph/                  # LangGraph 编排
    │   │   ├── builder.py          # build_graph() — 5 节点 StateGraph
    │   │   ├── nodes.py            # 各节点实现
    │   │   └── state.py            # AgentState TypedDict
    │   ├── identity/               # 身份识别领域 (v0.3.0)
    │   │   ├── models.py           # Actor / UserGroup / IdentityStrategy
    │   │   ├── resolver.py         # 身份解析器
    │   │   ├── plugin.py           # 身份插件基类 (v0.3.1)
    │   │   └── plugin_registry.py  # 插件发现 (v0.3.1)
    │   ├── memory/                 # 记忆领域模型
    │   │   ├── models.py           # MemoryEntry / MemoryType / DecayState / Relationship
    │   │   ├── context.py          # 主对话上下文装填
    │   │   ├── lifecycle.py        # 衰减/召回策略 (MemoryLifecycle)
    │   │   ├── reindex.py          # Reindexer + ReindexProgress + should_prune
    │   │   ├── short_term.py       # 短期记忆装填 (双窗: 时间窗 + 模型窗)
    │   │   ├── audience.py         # 受众过滤 (AudienceFilter) (v0.3.0)
    │   │   ├── expression_style.py # 表达习惯提取 (确定性, 零 LLM)
    │   │   └── trigger_reason.py   # 触发原因推断
    │   ├── models/                 # 角色 → 候选解析
    │   │   └── resolver.py         # RoleResolver (from role_bindings)
    │   ├── persona/                # 结构化人格定义 (v0.3.3)
    │   │   └── definition.py       # PersonaDefinition / PersonaIdentity / PersonaOverride
    │   └── tools/                  # 内部工具注册表 (v0.3.3)
    │       ├── internal_registry.py # InternalToolRegistry
    │       └── identity_binding.py  # 跨平台身份绑定内部 tool
    │
    ├── infra/                      # 基础设施层
    │   ├── forwarder/              # 上游 LLM 请求转发
    │   │   ├── forwarder.py        # Forwarder (单服务商)
    │   │   ├── multi.py            # MultiForwarder (多候选 + fallback)
    │   │   ├── connection_pool.py  # HTTP 连接池
    │   │   └── debug_hook.py       # 调试事件钩子
    │   ├── llm_service/            # LLM 服务商 + role_bindings
    │   │   ├── models.py           # LLMServiceProvider / ModelConfiguration / RoleBinding
    │   │   └── store.py            # SQLite 存储
    │   ├── vector_store.py         # ChromaDB 封装 + embedding lock
    │   ├── extraction.py           # 消息提取 (保留, 无主路径消费者)
    │   ├── debug_bus.py            # 调试事件总线 (SSE 订阅者)
    │   ├── debug_context.py        # contextvars (correlation_id / use_agent)
    │   ├── space_lock.py           # 空间级串行锁 (v0.3.3)
    │   ├── character_card.py       # SillyTavern 角色卡导入 (v0.3.3)
    │   └── crypto.py               # 加密工具
    │
    ├── persistence/                # 持久化层 (SQLite)
    │   ├── base.py                 # SqliteStore 基类 (长/短连接 + PRAGMA)
    │   ├── migrations.py           # MigrationRunner 迁移运行器
    │   ├── api_key_store.py        # SqliteApiKeyStore
    │   ├── auth_store.py           # SqliteAuthStore
    │   ├── conversation_store.py   # SqliteConversationStore (append-only turns)
    │   ├── memory_store.py         # SqliteMemoryStore
    │   ├── llm_service_store.py    # (alias → src.infra.llm_service.store)
    │   ├── http_log_store.py       # HttpLogStore
    │   ├── identity_store.py       # SqliteIdentityStore (actors / user_groups / ...)
    │   ├── idempotency_store.py    # SqliteIdempotencyStore
    │   ├── notification_store.py   # NotificationStore
    │   ├── persona_store.py        # PersonaStore (personas + persona_versions) (v0.3.3)
    │   ├── lorebook_store.py       # LorebookStore (v0.3.3)
    │   └── space_policy_store.py   # SpacePolicyStore (v0.3.3)
    │
    ├── tools/                      # 供 Agent 调用的工具函数
    │   ├── vector_search.py        # 向量检索工具
    │   ├── emotion_analyzer.py     # 情绪分析 (预计算, 非 ReAct 工具)
    │   ├── update_addressing.py    # 更新称呼工具
    │   └── sentence_classifier.py  # 句子分类 (保留, 无主路径消费者)
    │
    └── resources/personas/
        └── default.toml            # 默认人格资源 (打包进 wheel)
```

---

## 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| CLI | `src/cli/` | 命令行入口 (serve / login / 交互 shell / prompt 覆盖 / 身份管理) |
| API | `src/api/` | HTTP 路由 (/v1 对外 + /panel 面板)、鉴权、lifespan、debug、工具策略 |
| Core | `src/core/` | 业务逻辑: 配置、Agent、LangGraph 编排、记忆领域模型、角色→候选解析、提示词两层存储、身份识别、结构化人格、内部工具注册表 |
| Infra | `src/infra/` | 与外部系统交互: 上游转发 (多候选)、LLM 服务商元数据、向量库、消息提取、debug 事件总线、空间锁、角色卡导入 |
| Persistence | `src/persistence/` | SQLite 存储实现 (auth / api_key / memory / conversation / identity / idempotency / persona / lorebook / space_policy / ...) |
| Tools | `src/tools/` | 供 Agent 通过工具调用触发的功能 |

## 命名与导入约定

- 目录 / 模块使用 snake_case。
- 尽量**直接从子模块导入** (`from src.persistence.api_key_store import ...`)，不要依赖包 `__init__` 的 re-export。
- 保留 re-export 的包: `src.api` (对外挂载 router) 与 `src.core.agents` / `src.core.memory` / `src.infra` / `src.core.agents.prompts` (供 factory 与 graph 内部聚合)。

## 数据目录

`data/` 下的 SQLite 与 ChromaDB 由 `python -m src.main init-internal` 初始化；结构由各 `*Store.init_db()` 或 lifespan 阶段的 `connect()` 负责，无独立 migration 目录 (schema 有增列时走 `MigrationRunner` 幂等升级)。
