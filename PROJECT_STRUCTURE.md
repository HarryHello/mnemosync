# Mnemosync 项目目录结构

以下反映当前 (v0.2) 代码仓库的实际目录结构。

```
mnemosync/
├── LICENSE
├── README.md
├── PROJECT_STRUCTURE.md            # 本文件
├── Dockerfile
├── docker-compose.yml
├── install.sh                      # 单行部署脚本 (克隆到 ~/.mnemosync)
├── config.example.toml             # 配置模板
├── config.local.toml               # 本地配置 (git 忽略)
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
│   ├── api_keys.db                 # API Key SQLite 库
│   ├── auth.db                     # 用户认证 SQLite 库
│   ├── memory.db                   # 记忆元数据 SQLite 库
│   ├── llm_service.db              # LLM 服务商配置 SQLite 库
│   ├── http_logs.db                # 请求日志 (--debug)
│   └── chroma/                     # ChromaDB 向量索引
│
├── scripts/                        # 开发脚本 (探针 / 连通性测试)
│   ├── probe_function_call.py
│   ├── test_agents.py
│   └── test_connectivity.py
│
├── ui/                             # 前端 (独立子项目)
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
    │   ├── __init__.py             # 组装 api_router + forward_router
    │   ├── middleware.py           # HTTP 日志中间件
    │   ├── reasoning_control.py    # 代理推理决策 (should_use_proxy_thinking)
    │   ├── routes/
    │   │   ├── admin.py            # /api/v1/admin/* (全部 Depends(get_current_user), 含 prompts)
    │   │   ├── api_key.py          # /api/v1/api-keys/*
    │   │   ├── auth.py             # /auth/*
    │   │   └── forward.py          # /v1/chat/completions (OpenAI 兼容)
    │   └── schemas/                # Pydantic 请求/响应模型
    │       ├── admin.py            # Prompt* / 面板相关
    │       ├── api_key.py
    │       ├── auth.py
    │       └── forward.py
    │
    ├── core/                       # 核心业务层
    │   ├── config.py               # 配置加载 (TOML + env)
    │   ├── config_writer.py        # 运行期回写 config.local.toml
    │   ├── prompts/                # 提示词两层存储 (defaults + 用户覆盖)
    │   │   ├── registry.py         # PROMPT_REGISTRY (8 项 PromptSpec 白名单)
    │   │   └── store.py            # PromptStore (无缓存读盘 + 校验 + 备份)
    │   ├── agents/                 # LangGraph Agent
    │   │   ├── base.py             # ReAct 循环 / simple completion
    │   │   ├── factory.py          # run_main_dialogue / run_memory_analysis / ...
    │   │   └── prompts/            # 各 Agent 的 prompt builder (走 PromptStore)
    │   │       ├── defaults/       # 默认提示词 Markdown (随包发布, 8 个文件)
    │   │       │   ├── memory_analysis.md
    │   │       │   ├── memory_analysis_decay_header.md
    │   │       │   ├── relationship_analysis.md
    │   │       │   ├── prompt_cleaning_system.md
    │   │       │   ├── prompt_cleaning_user.md
    │   │       │   ├── proxy_thinking.md
    │   │       │   ├── sentence_classifier.md
    │   │       │   └── main_dialogue_frame.md
    │   │       ├── memory_analysis.py
    │   │       ├── prompt_cleaning.py
    │   │       ├── proxy_thinking.py
    │   │       └── relationship_analysis.py
    │   ├── graph/                  # LangGraph 编排
    │   │   ├── builder.py          # build_graph()
    │   │   ├── nodes.py            # 各节点实现
    │   │   └── state.py            # AgentState TypedDict
    │   └── memory/                 # 记忆领域模型
    │       ├── context.py          # build_main_dialogue_messages (走 main_dialogue_frame 模板)
    │       ├── lifecycle.py        # 衰减/召回策略
    │       └── models.py           # MemoryEntry / Visibility
    │
    ├── infra/                      # 基础设施层
    │   ├── extraction.py           # 从 OpenAI 消息数组提取用户轮次
    │   ├── forwarder/              # 上游 LLM 请求转发
    │   │   ├── connection_pool.py
    │   │   └── forwarder.py        # Forwarder + UpstreamError/UpstreamTimeout
    │   ├── llm_service/            # LLM 服务商配置
    │   │   ├── models.py           # LLMServiceProvider / ModelConfiguration
    │   │   └── store.py            # SQLite 存储
    │   └── vector_store.py         # ChromaDB 封装
    │
    ├── persistence/                # 持久化层 (SQLite)
    │   ├── api_key_store.py        # SqliteApiKeyStore
    │   ├── auth_store.py           # SqliteAuthStore (用户 + Session)
    │   └── memory_store.py         # SqliteMemoryStore
    │
    └── tools/                      # 供 Agent 调用的工具函数
        ├── emotion_analyzer.py
        ├── sentence_classifier.py  # 单句分类 (提示词清洗 Agent 用)
        ├── time_decay_calculator.py
        └── vector_search.py
```

**运行期数据 (data/)**: 除已列的 SQLite / Chroma 外, v0.2.1 起还有 `data/prompts/` (Agent 提示词用户覆盖层, gitignore) 及 `data/prompts/.history/` (每个 name 保留最近 10 份备份)。

---

## 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| CLI | `src/cli/` | 命令行入口 (serve / login / 交互 shell) |
| API | `src/api/` | HTTP 路由、鉴权、请求/响应模型 |
| Core | `src/core/` | 业务逻辑: 配置、Agent、LangGraph 编排、记忆领域模型 |
| Infra | `src/infra/` | 与外部系统交互: 上游转发、LLM 服务商、向量库、消息解析 |
| Persistence | `src/persistence/` | SQLite 存储实现 |
| Tools | `src/tools/` | 供 Agent 通过工具调用触发的功能 |

## 命名与导入约定

- 目录 / 模块使用 snake_case。
- 尽量**直接从子模块导入** (`from src.persistence.api_key_store import ...`),不要依赖包 `__init__` 的 re-export;历史上曾积累一批未使用的 re-export,现已清理。
- 唯一保留 re-export 的包是 `src.api` (对外挂载 router) 与 `src.core.agents` / `src.core.memory` / `src.infra` / `src.core.agents.prompts` (供 factory 与 graph 内部聚合)。

## 数据目录

`data/` 下的 SQLite 与 ChromaDB 由 `python -m src.main init-internal` 初始化;结构由各 `*Store.init_db()` 负责,无独立 migration 目录。
