# Mnemosync — Agent 指南

## Project Overview

**Mnemosync** (v0.3.4) 是一个基于 **LangGraph** 多 Agent 编排的跨平台人格记忆同步代理服务器。它在网络层拦截 OpenAI 兼容请求，把所有前端的对话汇聚成一条连续流后再转发给上游 LLM，同时维护长期记忆、关系演化、多用户身份识别与幂等重放。

核心哲学：**服务器持有真相** —— 人格由服务器权威持有，对话流水永不丢失，跨前端连续对话。

项目使用 **AGPL-3.0** 开源协议。作者：HarryHelloo。

---

## Technology Stack

| 层 | 技术 | 说明 |
|---|---|---|
| **语言** | Python 3.12+ | 强类型，pyright/mypy 严格模式 |
| **Web 框架** | FastAPI + uvicorn | HTTP API 层 |
| **Agent 编排** | LangGraph | StateGraph 多节点编排 |
| **LLM 集成** | langchain-core, langchain-openai | 多服务商转发 |
| **向量存储** | ChromaDB | 语义检索 + embedding lock 机制 |
| **关系型存储** | SQLite via aiosqlite | 多库分离 (auth / api_key / memory / conversation / identity / ...) |
| **配置** | TOML (tomllib) | 三层覆盖: 资源默认值 → config.local.toml → data/persona_override.toml |
| **认证** | passlib[bcrypt] + python-jose | 面板登录 / API Key 双通道 |
| **加密** | cryptography | API Key 加密存储 |
| **日志** | structlog + 标准 logging | 结构化日志 |
| **前端 (UI)** | Vue 3 + Element Plus + Pinia + TypeScript | 管理面板 |
| **构建** | hatchling (Python), Vite (UI) | |
| **包管理** | uv (Astral) | 锁文件: uv.lock |
| **容器** | Docker (multi-stage: Node 24 build → Python 3.12-slim) | |
| **CI** | GitHub Actions | ruff → mypy → pytest → pip-audit (backend); type-check → lint → test:unit → build → npm audit (frontend) |

---

## Project Structure

```
mnemosync/
├── pyproject.toml              # Python 项目元数据 + uv 依赖
├── uv.lock                     # 依赖锁定
├── config.example.toml         # 配置模板 (安全可提交)
├── config.local.toml           # 本地配置 (gitignored, 可缺失)
├── Dockerfile                  # 多阶段构建 (UI → Python)
├── docker-compose.yml          # 一键部署
├── install.sh                  # 单行安装脚本
├── .env.example                # 环境变量模板
├── pyrightconfig.json          # Pyright 配置
│
├── src/                        # 后端 Python 源码
│   ├── main.py                 # 兼容旧调用方式的入口
│   │
│   ├── cli/                    # 命令行入口
│   │   ├── cli.py              # `mnemosync` 顶层 CLI 入口
│   │   ├── cli_interactive.py  # `mnemosync login` 交互式 shell
│   │   ├── ask.py              # 调试: 直连 LangGraph 跑一次主对话
│   │   ├── prompt_cmd.py       # 提示词覆盖管理子命令
│   │   └── identity_cmd.py     # 身份管理子命令
│   │
│   ├── api/                    # HTTP API 层 (FastAPI)
│   │   ├── __init__.py         # 组装 /panel 和 /v1 路由
│   │   ├── lifespan.py         # 应用启动/关闭: 打开 store 长连接 + 后台清理任务
│   │   ├── deps.py             # FastAPI 依赖注入
│   │   ├── middleware.py       # HTTP 日志 + 调试总线 emit
│   │   ├── reasoning_control.py # 代理推理决策 + 自适应缓存 + SSE 合成
│   │   ├── tool_policies.py    # 工具策略过滤
│   │   ├── tool_transactions.py # 工具续轮 (tool_roundtrip) 尾部校验
│   │   ├── state.py            # AppState 数据类
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   └── routes/             # 路由实现
│   │       ├── forward/        # /v1/chat/completions + /v1/models
│   │       │   ├── __init__.py # 主路由逻辑
│   │       │   ├── stream.py   # 流式处理
│   │       │   ├── nonstream.py # 非流式处理
│   │       │   ├── identity.py # 身份解析逻辑
│   │       │   ├── idempotency.py # 幂等重放
│   │       │   ├── persistence.py # 事件落库
│   │       │   └── memory_graph.py # 异步记忆图
│   │       ├── admin*.py       # 管理面板各模块路由
│   │       ├── api_key.py      # API Key 管理
│   │       └── auth.py         # 认证路由
│   │
│   ├── core/                   # 核心业务逻辑
│   │   ├── config.py           # 配置加载 (Settings 单例, 三层优先级)
│   │   ├── constants.py        # 全局常量
│   │   ├── prompts/            # 提示词两层存储 (默认 + 用户覆盖)
│   │   │   ├── registry.py     # PROMPT_REGISTRY 白名单
│   │   │   └── store.py        # PromptStore (读盘 + 校验 + 备份)
│   │   ├── agents/             # LangGraph Agent 执行函数
│   │   │   ├── base.py         # ReAct 循环 / simple completion
│   │   │   ├── factory.py      # Agent 工厂 (main/memory/relationship/expressor...)
│   │   │   └── prompts/        # Prompt builder
│   │   │       ├── defaults/   # 默认提示词 Markdown
│   │   │       └── *.py        # builder 函数
│   │   ├── graph/              # LangGraph 编排
│   │   │   ├── builder.py      # build_graph() — 5 节点 StateGraph
│   │   │   ├── nodes.py        # 各节点实现
│   │   │   └── state.py        # AgentState TypedDict
│   │   ├── models/             # 角色 → 候选解析
│   │   │   └── resolver.py     # RoleResolver
│   │   ├── memory/             # 记忆领域模型
│   │   │   ├── models.py       # MemoryEntry / MemoryType / DecayState / Relationship
│   │   │   ├── context.py      # 主对话上下文装填
│   │   │   ├── lifecycle.py    # 衰减/召回策略
│   │   │   ├── reindex.py      # Reindexer + ReindexProgress
│   │   │   ├── short_term.py   # 短期记忆装填 (双窗: 时间窗 + 模型窗)
│   │   │   ├── audience.py     # 受众过滤 (AudienceFilter)
│   │   │   ├── expression_style.py # 群聊表达习惯提取
│   │   │   └── trigger_reason.py # 对话触发原因推断
│   │   ├── identity/           # 身份识别领域 (v0.3.0)
│   │   │   ├── models.py       # Actor / UserGroup / IdentityStrategy
│   │   │   ├── resolver.py     # 身份解析器
│   │   │   ├── plugin.py       # 身份插件基类
│   │   │   └── plugin_registry.py # 插件发现
│   │   ├── persona/            # 人格结构化定义 (v0.3.3+)
│   │   │   └── definition.py   # PersonaDefinition / PersonaIdentity / PersonaOverride
│   │   └── tools/              # 内部工具注册表
│   │       ├── internal_registry.py # InternalToolRegistry
│   │       └── identity_binding.py  # 跨平台身份绑定工具
│   │
│   ├── infra/                  # 基础设施层
│   │   ├── forwarder/          # 上游 LLM 转发
│   │   │   ├── forwarder.py    # Forwarder (单服务商)
│   │   │   ├── multi.py        # MultiForwarder (多候选 + fallback)
│   │   │   ├── connection_pool.py # HTTP 连接池
│   │   │   └── debug_hook.py   # 调试事件钩子
│   │   ├── llm_service/        # LLM 服务商元数据
│   │   │   ├── models.py       # LLMServiceProvider / ModelConfiguration / RoleBinding
│   │   │   └── store.py        # SQLite 存储
│   │   ├── vector_store.py     # ChromaDB 封装 + embedding lock
│   │   ├── extraction.py       # 从 OpenAI 消息数组提取用户轮次
│   │   ├── debug_bus.py        # 调试事件总线 (SSE 订阅者)
│   │   ├── debug_context.py    # contextvars (correlation_id / use_agent)
│   │   ├── space_lock.py       # 空间级串行锁 SpaceLockManager
│   │   ├── character_card.py   # SillyTavern 角色卡导入
│   │   └── crypto.py           # 加密工具
│   │
│   ├── persistence/            # 持久化层 (SQLite)
│   │   ├── base.py             # SqliteStore 基类 (长/短连接 + PRAGMA)
│   │   ├── migrations.py       # MigrationRunner 迁移运行器
│   │   ├── *.store.py          # 各领域存储: auth / api_key / memory / conversation /
│   │   │                       #   identity / idempotency / http_log / notification /
│   │   │                       #   persona / lorebook / space_policy
│   │   └── ...                 # (详见各文件)
│   │
│   ├── tools/                  # 供 Agent 调用的工具函数
│   │   ├── vector_search.py    # 向量检索工具
│   │   ├── emotion_analyzer.py # 情绪分析
│   │   ├── update_addressing.py # 更新称呼工具
│   │   └── sentence_classifier.py # 句子分类 (提示词清洗使用)
│   │
│   └── resources/personas/
│       └── default.toml        # 默认人格资源 (打包进 wheel)
│
├── tests/                      # 测试目录
│   ├── conftest.py             # 顶层 fixtures (重置 Settings 单例)
│   ├── unit/                   # 单元测试 (70+ 文件)
│   ├── persistence/            # 持久层测试
│   └── api/                    # API 集成测试
│
├── ui/                         # 前端 Vue 3 子项目
│   ├── package.json            # Node 24+, Vite 8, Vue 3.5, Element Plus
│   ├── src/
│   │   ├── views/              # 页面组件
│   │   ├── components/         # 通用组件
│   │   ├── api/                # API 调用层
│   │   ├── stores/             # Pinia stores
│   │   ├── router/             # Vue Router
│   │   ├── types/              # TypeScript 类型
│   │   ├── utils/              # 工具函数
│   │   ├── layouts/            # 布局组件
│   │   └── scss/               # 全局样式
│   └── dist/                   # 构建产物 (gitignored)
│
├── plugins/                    # 身份解析插件
│   └── astrbot.py              # AstrBot QQ 适配器
│
├── scripts/                    # 开发脚本
│   ├── probe_function_call.py  # 服务商 function calling 探针
│   ├── test_agents.py          # Agent 连通性探针
│   ├── test_connectivity.py    # 连通性测试
│   ├── seed_mock_memories.py   # 注入 mock 记忆
│   └── mock_notifications.py   # 模拟通知
│
└── data/                       # 运行时数据 (gitignored)
    ├── api_keys.db / auth.db / memory.db / llm_service.db
    ├── conversation.db / http_logs.db / identity.db
    ├── idempotency.db / notifications.db
    ├── chroma/                 # ChromaDB 向量索引
    └── prompts/                # 提示词用户覆盖层
```

---

## Build & Test Commands

### Backend

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest                                          # 全部测试, 含 coverage
uv run pytest tests/unit/test_identity_resolver.py      # 单文件
uv run pytest -k "test_something"                      # 按名称匹配

# Lint
uv run ruff check src tests                             # ruff lint
uv run ruff check src tests --fix                       # 自动修复

# Type check
uv run mypy src                                          # mypy 严格模式

# Run server (development)
uv run mnemosync serve                                  # 前台开发
uv run mnemosync serve --debug                          # HTTP 请求/响应日志

# Run CLI
uv run mnemosync                                        # 帮助
uv run mnemosync ask "你好" --user harry                # 直连 LangGraph 调试

# Init database
uv run mnemosync init

# Interactive shell
uv run mnemosync login

# Coverage
uv run pytest --cov=src --cov-report=term-missing       # 终端
uv run pytest --cov=src --cov-report=html               # HTML 报告
```

### Frontend

```bash
cd ui

npm install              # 或 npm ci (CI)
npm run dev              # 开发服务器
npm run build            # 生产构建
npm run test:unit        # 单元测试 (vitest)
npm run type-check       # TypeScript 类型检查
npm run lint             # oxlint + eslint
npm run format           # prettier 格式化
npm run preview          # 预览构建产物
```

### Docker

```bash
docker compose up -d              # 启动
docker compose exec mnemosync mnemosync init   # 初始化
docker compose logs -f            # 查看日志
docker compose down               # 停止
```

---

## LangGraph Graph Topology

```
parse_request → [proxy_thinking?] → main_dialogue
                                     ↓ (并行)
                              relationship_analysis + memory_analysis → END
```

**5 个节点:**
1. **parse_request** — 消息提取 + 用户标识解析 (轻量, 无 LLM)
2. **proxy_thinking** — 可选代理思考 Agent (为不具备原生推理的模型补齐 CoT)
3. **main_dialogue** — 主对话 Agent: 加载记忆 + 拼装上下文 + 调用 MAIN 角色 LLM
4. **relationship_analysis** — 关系分析 Agent: 计算亲密度/信任度增量
5. **memory_analysis** — 记忆分析 Agent: 提取候选记忆 + 确定性衰减

节点之间通过 `AgentState` (TypedDict) 通信。共享 store 通过 `config["configurable"]` 传入。

---

## Key Architecture Decisions

### Server-First Persona
- 人格由服务器 `[persona]` 段权威定义
- 支持**多个人格 profile** (`personas` 表)，可在面板切换
- 每个人格 profile 维护独立版本链 (`persona_versions`)
- `PersonaIdentity` 只含人格级字段 (personality/speaking_style/values/persona_addressing)
- **per-user 称呼与关系背景** (user_addressing/context) 由 `Relationship` 模型维护，不在人格定义中
- 空间覆盖 (`PersonaOverride`) 仅覆盖 speaking_style / personality / scenario
- 客户端 system 消息走**提示词清洗 Agent** 剥离人格描述、保留功能性指令

### Multi-Persona Profiles
- `personas` 表: 人格注册表，管理多个人格 profile 共存
- 每个人格 profile 有独立的版本历史 (`persona_versions.persona_id`)
- 切换人格: 通过 API/Panel 设置 `personas.is_active=1`
- 初始迁移自动将已有版本关联到默认人格
- 删除人格级联删除其所有版本

### Role Bindings (v0.2.3+)
- `main` / `assist` / `embedding` / `rerank` 四种角色
- 每个角色维护优先级候选列表 (存 `role_bindings` 表)
- 主对话与辅助 Agent 上游失败自动 fallback 到下一候选
- **嵌入角色单绑定**: 换嵌入模型必须走 Reindex 走完再服务
- 模型绑定完全由 DB 管理

### Cross-Platform Identity (v0.3.0)
- API Key 绑定**身份识别策略**: direct / api_key_bound / regex / llm / plugin
- 从请求中提取参与者 (Actor): AstrBot QQ 号、ChatBox 固定用户、或语义识别
- 跨平台身份归一: 同一人在不同平台的 Actor 可绑入一个 UserGroup
- 群聊按空间 (space) 分区成独立对话流
- 记忆检索先按受众过滤再交给模型
- 未绑定策略的 Key 进入**非归属模式**

### Short-Term Memory Dual-Window (v0.2.6)
- 时间窗: 默认 7 天内的 `conversation_turns`
- 模型窗: 按 `context_length` 从最旧那端裁剪
- 客户端 UI "清空" 不影响服务器记忆

### Idempotency (v0.3.0)
- 平台重发消息按事件 ID 幂等重放
- 命中幂等时不产生任何 LLM 开销与记忆副作用
- 空响应不缓存

### Proxy Thinking (v0.2.0+)
- 为不具备原生推理的模型补齐 reasoning_content
- 自适应缓存: 遇到上游吐 reasoning_content 就记住, 下次跳过
- 前缀模式配置: `proxy_thinking_native_reasoning_models`

---

## Code Style Guidelines

- **Python**: Python 3.12+ 严格类型注解
- **导入风格**: 尽量直接从子模块导入, 不依赖 `__init__` re-export
- **命名**: snake_case (目录/模块/变量), PascalCase (类), SCREAMING_SNAKE (常量)
- **行长度**: 100 字符 (ruff line-length=100, E501 ignored)
- **Lint**: ruff (E/W/F/I/B/C4/UP) 选择集
- **类型检查**: mypy strict + disallow_untyped_defs + warn_return_any
- **依赖注入**: 使用 FastAPI `app.state` 和 `Depends`; LangGraph config 中传 store
- **错误处理**: 上层调用方处理异常, 节点内不吞 error; 辅助 Agent 失败时降级不影响主对话

### Naming & Import Conventions
- 目录/模块使用 snake_case
- 尽量直接从子模块导入 (`from src.persistence.api_key_store import ...`), 不要依赖包 `__init__` 的 re-export
- 保留 re-export 的包: `src.api`, `src.core.agents`, `src.core.memory`, `src.infra`, `src.core.agents.prompts`

---

## Testing Strategy

- **框架**: pytest + pytest-asyncio (asyncio_mode=auto)
- **Coverage**: 默认基于 `--cov=src --cov-report=term-missing`
- **Fixtures**: 顶层 `conftest.py` 每个用例前后重置 Settings 单例
- **测试位置**: `tests/unit/` (业务逻辑), `tests/persistence/` (存储层), `tests/api/` (集成)
- **测试风格**: 浅依赖 mock, 重点测试业务逻辑层
- **CI 命令**: `ruff check src tests` → `mypy src` → `pytest --cov=src --cov-report=term-missing` → `pip-audit`
- **前端测试**: vitest 单元测试, 位于 `ui/src/` 同层

---

## Database Schema Management

- SQLite 多库分离: auth / api_key / memory / conversation / http_log / identity / idempotency / notification
- 结构由各 `*Store._init_schema()` 负责
- 迁移使用 `MigrationRunner` (存 `_migrations` 表): `add_column_if_missing()` 幂等增列
- 迁移列于 store 类的 `_MIGRATIONS` 列表

---

## Important Config Files

| 文件 | 用途 |
|---|---|
| `pyproject.toml` | 项目元数据、依赖、ruff/mypy/pytest 配置 |
| `config.example.toml` | 配置模板 (安全可提交) |
| `config.local.toml` | 本地配置 (gitignored, 可缺失) |
| `src/resources/personas/default.toml` | 默认人格 (打包进 wheel) |
| `data/persona_override.toml` | 面板写入的人格覆盖 (优先级最高) |
| `ui/package.json` | 前端依赖和构建脚本 |
| `Dockerfile` | 多阶段 Docker 构建 |
| `.github/workflows/ci.yml` | CI pipeline |
| `.github/workflows/release.yml` | Release pipeline |
| `pyrightconfig.json` | Pyright 配置 |

---

## Deployment

- **Docker**: `docker compose up -d` (多阶段: Node 24 build → Python 3.12-slim)
- **源码**: `uv sync && uv run mnemosync init && uv run mnemosync serve`
- **install.sh**: 一键脚本 (`~/.mnemosync` 安装)
- **默认端口**: 16125
- **健康检查**: `GET /health`
- **面板默认账号**: `mnemosync` / `mnemosync` (首次登录后需改)

---

## Security Considerations

- **API Key 加密存储**: 使用 `cryptography` 库加密后落 SQLite
- **认证双通道**:
  - 管理面板: Session cookie + 密码 (bcrypt)
  - OpenAI 兼容层: Bearer token (API Key)
- **强制改密**: 首次登录面板, 未改密码时面板管理路由返回 403
- **请求头脱敏**: 日志中间件脱敏 Authorization 等敏感头
- **提示词路径安全**: PROMPT_REGISTRY 白名单防止路径穿越
- **幂等安全**: 幂等缓存不存储敏感信息
- **跨域**: CORS 中间件全放通 (`allow_origins=["*"]`)
- **SQLite WAL**: WAL + NORMAL 同步模式保证写安全
- **空间锁**: 同一空间内的请求串行处理, 不同空间并行, 避免竞态
