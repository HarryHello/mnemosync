# Mnemosync 项目目录结构

```
mnemosync/
├── .git/                          # Git 版本控制
├── .github/                       # GitHub 配置
│   ├── workflows/
│   │   ├── ci.yml                 # CI 流水线
│   │   ├── release.yml            # 自动发布
│   │   └── docker.yml             # Docker 镜像构建
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
│
├── .idea/                         # IDE 配置 (JetBrains)
│
├── docs/                          # 文档目录
│   ├── architecture.md            # 架构设计文档
│   ├── configuration.md           # 配置说明
│   ├── memory-model.md            # 记忆模型设计
│   ├── deployment.md              # 部署指南
│   ├── api-reference.md           # API 接口文档
│   └── modules/                   # 模块文档
│       ├── forward.md             # 转发模块
│       ├── message-extraction.md  # 消息提取模块
│       ├── compression.md         # 上下文压缩模块
│       ├── injection.md           # 人格注入模块
│       ├── relationship.md        # 关系认知模块
│       ├── memory-manager.md      # 记忆管理模块
│       ├── access-policy.md       # 访问策略模块
│       └── small-llm.md           # 小模型服务模块
│
├── src/                           # 源代码目录
│   ├── __init__.py
│   ├── main.py                    # 程序入口
│   │
│   ├── core/                      # 核心服务
│   │   ├── __init__.py
│   │   ├── config.py              # 配置加载与验证
│   │   ├── logging.py             # 日志配置
│   │   └── exceptions.py          # 自定义异常
│   │
│   ├── api/                       # API 层
│   │   ├── __init__.py
│   │   ├── gateway.py             # API Gateway (请求入口)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── chat.py            # /v1/chat/completions
│   │   │   ├── models.py          # /v1/models
│   │   │   └── admin.py           # 管理接口
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py            # API Key 鉴权
│   │   │   ├── persona.py         # Persona ID 路由
│   │   │   └── rate_limit.py      # 限流中间件
│   │   └── schemas/               # Pydantic 模型
│   │       ├── __init__.py
│   │       ├── chat.py
│   │       ├── memory.py
│   │       └── relationship.py
│   │
│   ├── modules/                   # 功能模块
│   │   ├── __init__.py
│   │   │
│   │   ├── forward/               # 转发模块
│   │   │   ├── __init__.py
│   │   │   ├── forwarder.py       # 上游客户端
│   │   │   ├── connection_pool.py # 连接池管理
│   │   │   └── stream.py          # 流式响应透传
│   │   │
│   │   ├── extraction/            # 消息提取模块 (原去重模块)
│   │   │   ├── __init__.py
│   │   │   ├── extractor.py       # 消息提取器
│   │   │   └── matcher.py         # 消息匹配器
│   │   │
│   │   ├── compression/           # 上下文压缩模块
│   │   │   ├── __init__.py
│   │   │   ├── compressor.py      # 压缩策略
│   │   │   └── token_counter.py   # Token 计数
│   │   │
│   │   ├── injection/             # 人格注入模块
│   │   │   ├── __init__.py
│   │   │   └── injector.py        # 人格提示词注入
│   │   │
│   │   ├── relationship/          # 关系认知模块
│   │   │   ├── __init__.py
│   │   │   ├── relationship_manager.py
│   │   │   ├── state.py           # 关系状态模型
│   │   │   └── analyzer.py        # 亲密度语义分析
│   │   │
│   │   ├── memory/                # 记忆管理模块
│   │   │   ├── __init__.py
│   │   │   ├── manager.py         # 记忆存储抽象层
│   │   │   ├── models.py          # 记忆数据模型
│   │   │   └── visibility.py      # 可见性枚举
│   │   │
│   │   ├── policy/                # 访问策略模块
│   │   │   ├── __init__.py
│   │   │   ├── engine.py          # 策略执行引擎
│   │   │   ├── parser.py          # 自然语言策略解析
│   │   │   └── authorization.py   # 跨用户授权 (预留)
│   │   │
│   │   └── small_llm/             # 小模型服务模块
│   │       ├── __init__.py
│   │       ├── client.py          # 小模型客户端
│   │       └── tasks.py           # 小模型任务 (亲密度/策略解析)
│   │
│   ├── pipeline/                  # 上下文清洗引擎
│   │   ├── __init__.py
│   │   ├── pipeline.py            # Pipeline 编排
│   │   └── handlers/
│   │       ├── __init__.py
│   │       ├── extract.py         # 消息提取处理器
│   │       ├── sort.py            # 时间戳排序处理器
│   │       ├── compress.py        # 上下文压缩处理器
│   │       └── inject.py          # 人格注入处理器
│   │
│   ├── storage/                   # 持久化层
│   │   ├── __init__.py
│   │   ├── base.py                # 存储抽象基类
│   │   ├── sqlite.py              # SQLite 实现
│   │   ├── redis.py               # Redis 实现 (可选)
│   │   └── migrations/            # 数据库迁移
│   │       ├── 001_initial.sql
│   │       └── 002_relationships.sql
│   │
│   └── utils/                     # 工具函数
│       ├── __init__.py
│       ├── hash.py                # 哈希工具
│       ├── time.py                # 时间工具
│       └── token.py               # Token 工具
│
├── tests/                         # 测试目录
│   ├── __init__.py
│   ├── conftest.py                # pytest 配置
│   ├── unit/                      # 单元测试
│   │   ├── test_extractor.py
│   │   ├── test_compressor.py
│   │   ├── test_relationship.py
│   │   └── test_policy.py
│   ├── integration/               # 集成测试
│   │   ├── test_pipeline.py
│   │   └── test_api.py
│   └── fixtures/                  # 测试数据
│       ├── conversations.json
│       └── memories.json
│
├── scripts/                       # 脚本工具
│   ├── init_db.sh                 # 初始化数据库
│   ├── migrate.sh                 # 运行数据库迁移
│   └── dev_setup.sh               # 开发环境搭建
│
├── docker/                        # Docker 相关
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-compose.dev.yml
│
├── .env.example                   # 环境变量示例
├── .gitignore
├── .qwenignore
├── LICENSE
├── README.md
├── pyproject.toml                 # Python 项目配置 (Poetry)
├── requirements.txt               # 依赖列表 (pip 备用)
└── PROJECT_STRUCTURE.md           # 本文件
```

---

## 目录设计说明

### 核心分层

| 层级 | 目录 | 职责 |
|------|------|------|
| **API 层** | `src/api/` | 请求入口、鉴权、路由、数据校验 |
| **模块层** | `src/modules/` | 独立功能模块（转发、提取、压缩等） |
| **引擎层** | `src/pipeline/` | 编排多个模块形成清洗流水线 |
| **存储层** | `src/storage/` | 持久化抽象（SQLite/Redis） |

### 模块命名规范

- **文档**：`docs/modules/<module-name>.md`（kebab-case）
- **代码**：`src/modules/<module_name>/`（snake_case）
- **Python 包**：`from src.modules.extraction import Extractor`

### 关键模块对应关系

| 架构组件 | 文档 | 代码目录 |
|----------|------|----------|
| API Gateway | [architecture.md](docs/architecture.md) | `src/api/gateway.py` |
| Relationship Layer | [modules/relationship.md](docs/modules/relationship.md) | `src/modules/relationship/` |
| Context Pipeline | [architecture.md](docs/architecture.md#54-上下文清洗引擎-context-pipeline) | `src/pipeline/` |
| Message Extraction | [modules/message-extraction.md](docs/modules/message-extraction.md) | `src/modules/extraction/` |
| Memory Manager | [modules/memory-manager.md](docs/modules/memory-manager.md) | `src/modules/memory/` |
| Access Policy | [modules/access-policy.md](docs/modules/access-policy.md) | `src/modules/policy/` |
| Forwarder | [modules/forward.md](docs/modules/forward.md) | `src/modules/forward/` |
| Storage | [memory-model.md](docs/memory-model.md) | `src/storage/` |

---

## 开发优先级

### Phase 1: MVP (v0.1.0)
```
src/main.py
src/api/gateway.py
src/modules/forward/
src/modules/extraction/
src/storage/sqlite.py
```

### Phase 2: 核心功能 (v0.2.0)
```
src/modules/memory/
src/modules/relationship/
src/pipeline/
```

### Phase 3: 高级功能 (v0.3.0)
```
src/modules/policy/
src/modules/small_llm/
src/modules/compression/
```
