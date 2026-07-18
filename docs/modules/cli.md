# 命令行环境 | CLI

> **系统版本**: v0.2.7
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-25
> **最后更新**: 2026-07-18
> **作者**: HarryHelloo

---

## 1. 定位

CLI 是 Mnemosync 的日常管理入口: 初始化数据库、启停服务、生成/查看 API Key、维护 LLM 服务商与模型配置、命令行调试主对话。

- 顶层命令: [src/cli/cli.py](../../src/cli/cli.py)
- 交互式 shell: [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py)
- 调试命令 `ask` 实现: [src/cli/ask.py](../../src/cli/ask.py)
- 提示词覆盖管理 `prompt` 实现: [src/cli/prompt_cmd.py](../../src/cli/prompt_cmd.py)

---

## 2. 顶层命令

```
mnemosync <command> [options]
```

| 命令 | 说明 |
|------|------|
| `init [--docker]` | 初始化 SQLite 数据库 (`data/auth.db`, `data/api_keys.db`, `data/llm_service.db`, `data/memory.db`) |
| `serve [--host --port --daemon --debug --log-level]` | 启动 FastAPI 服务; `--debug` 打开上游 HTTP 请求/响应日志 |
| `stop` | 停止 Docker 模式下的服务 |
| `login [--docker]` | 用户名/密码登录, 进入交互式 shell |
| `ask [flags] "<question>"` | 命令行直连主对话 (调试用), 详见 [§5](#5-调试命令-ask) |
| `prompt <subcmd> ...` | 管理 Agent 提示词覆盖 (list/show/set/reset/validate), 详见 [§6](#6-提示词覆盖管理-prompt) |
| `upgrade [--branch <name>]` | 从 Git 拉取新版本 |
| `help` | 显示顶层帮助 |

**参数细节**:

- `serve --host` 默认 `0.0.0.0`, `--port` 默认 `16125`, `--log-level` 取 `debug/info/warning/error`
- `serve --debug` 与 `ask --debug` 通过 `MNEMOSYNC_DEBUG=1` 环境变量控制 Forwarder 输出
- `upgrade --branch` 默认 `dev`

顶层命令**不**包含 API Key / 服务商管理; 那些命令在交互式 shell 内。

---

## 3. 初始化流程

```bash
mnemosync init
```

输出:
```
Mnemosync initializing...
Success!
Use `mnemosync login` to start the CLI environment,
or use `mnemosync help` to get more information.
```

`init` 会调 [SqliteAuthStore.init_db](../../src/persistence/auth_store.py) / [SqliteApiKeyStore.init_db](../../src/persistence/api_key_store.py) / [LLMServiceStore.init_db](../../src/infra/llm_service/store.py) / [MemoryStore.init_db](../../src/persistence/memory_store.py)。默认管理员 `mnemosync/mnemosync` 首次成功登录时自动创建。

---

## 4. 交互式 Shell

```bash
mnemosync login
```

登录成功后的 Banner (真实字符见 [cli_interactive.py:87](../../src/cli/cli_interactive.py#L87)):

```
╭───────────────────────────────────────────────────────────────╮
│  ... Mnemosync ASCII art ...                                  │
│                         Mnemosync                             │
│                         v0.2.0                                │
╰───────────────────────────────────────────────────────────────╯
```

> Banner 版本字符串来自代码常量, 与整体系统版本可能有一版滞后。

Shell 内命令 (真实分发见 [cli_interactive.py:633](../../src/cli/cli_interactive.py#L633)):

### 4.1 通用

| 命令 | 说明 |
|------|------|
| `help` | 显示可用命令 |
| `logout` | 退出 shell (后台服务保持运行) |
| `stop` | 停止 Mnemosync 服务 |
| `show-config` | 打印当前 `config.local.toml` 关键字段 |

### 4.2 调试

| 命令 | 说明 |
|------|------|
| `ask [flags] "<question>"` | 与顶层 `mnemosync ask` 同一实现, 参数见 [§5](#5-调试命令-ask) |

### 4.3 API Key

| 命令 | 说明 |
|------|------|
| `ls-keys` | 列出所有 API Key (脱敏) |
| `show-key <key_id>` | 显示单个 Key 明文 |
| `generate-key` | 生成新 Key, 交互输入备注 |

> 撤销 Key 走 HTTP: `DELETE /api-keys/{id}` 或 `POST /api-keys/revoke`; 见 [api-key.md](api-key.md)。

### 4.4 LLM 服务商

| 命令 | 说明 |
|------|------|
| `ls-service` | 列出服务商 |
| `ad-service` | 添加服务商 (交互输入 id / base_url / api_key) |
| `show-service <srv_id>` | 服务商详情 |
| `rm-service <srv_id>` | 删除服务商 (级联删除模型配置) |
| `ls-models <srv_id>` | 通过 Forwarder 拉取服务商 `/models` |

### 4.5 模型绑定 (v0.2.3 role_bindings 表, 热更新)

从 v0.2.3 起, 模型不再写在 `config.local.toml`, 而是存 `role_bindings` 表 (main / assist / embedding / rerank 四个角色, 每个角色可有多条按 priority 排序的候选)。CLI 通过 [LLMServiceStore](../../src/infra/llm_service/store.py) 直接读写, 修改**立即生效**, 无需重启。

| 命令 | 说明 |
|------|------|
| `model ls [role]` | 列出所有角色绑定 (含 priority / ctx / dim / send-dim), 可按角色过滤 |
| `model add <role> <srv_id> <model> [--priority N] [--context N] [--dim N] [--send-dim]` | 追加候选; `--context` 面板展示; `--dim` 锁向量库维度 (v0.2.4); `--send-dim` 才把维度作为 `dimensions` 参数发给上游 (v0.2.8, 仅可变维模型需要, 详见 [llm-service.md §2.6](llm-service.md#26-send_dimensions-透传开关-v028)) |
| `model rm <role> <priority>` | 删除某个优先级候选 |
| `model reorder <role> <srv:model,srv:model,...>` | 重新排序候选 (embedding 角色被拒绝, 单绑定无意义) |
| `model test <role>` | 探活: 用该角色的首位候选发一次最小请求 |
| `test-model <srv_id> <model>` | 探活: 不看角色, 直接指定服务商 + 模型 |

**角色约束 (v0.2.4)**: 添加第二条 `embedding` 绑定会被拒绝 (ValueError); 想换嵌入模型必须先 `model rm embedding <priority>` 删旧的, 再 `model add`, 然后走 `memory reindex` 重建向量库 (见 §4.6)。见 [llm-service.md §2.5](llm-service.md#25-嵌入角色单绑定-v024)。

### 4.6 记忆维护 (v0.2.4)

维护命令通过面板 HTTP 触发 (需要 CLI 已登录管理员账号, 会自动用当前账号换 panel JWT):

| 命令 | 说明 |
|------|------|
| `memory reindex [--prune] [--threshold F]` | 重建全量向量 (换嵌入模型后必跑). `--prune` 顺便清理低价值记忆 (阈值默认 0.05, 走同一 `should_prune` 规则). 阻塞轮询到完成 |
| `memory prune [--threshold F] [--dry-run]` | 纯本地规则清理 (forgotten / expired / low_priority; permanent 不动). `--dry-run` 只返回统计, 不删 |
| `persona reset [--dry-run] [--yes]` (v0.2.7) | 回到"新装"状态: 清空所有长期记忆 (含 PERMANENT) / 关系 / 短期对话 / 向量库。保留服务商与 API Key。非 `--dry-run` 时先跑一次 dry-run 展示计数, 再要求输入 `yes` 二次确认; `--yes` 跳过交互确认 (脚本用) |

清理规则细节见 [memory-system.md](memory-system.md) 与 [dev-decisions.md 嵌入模型单绑定 + Reindex + Prune](../dev-decisions.md)。

---

## 5. 调试命令 `ask`

`ask` 是不走 HTTP 层的调试入口, 直接在本进程跑一次完整 LangGraph (加载记忆 → 主对话 → 记忆/关系分析), 用来观察 prompt / 状态 / 记忆读写。两种入口共用 `src.cli.ask.run_ask`:

1. 顶层: `mnemosync ask [flags] "<question>"`
2. Shell 内: 先 `mnemosync login`, 再 `ask [flags] "<question>"`

```bash
# 非流式一问一答
mnemosync ask "你好, 我叫 harry"

# 流式模式 (与生产 SSE 路径一致)
mnemosync ask --stream "记得我叫什么吗?"

# 打印上游 HTTP 请求/响应 JSON
mnemosync ask --debug "hello"

# 指定 source_user / 加载自定义人格
mnemosync ask --user harry --persona-file persona.txt "..."

# 走本地 HTTP 通路 (需 mnemosync serve 已运行)
mnemosync ask --via-http --api-key sk-xxx "..."
```

参数:

| flag | 作用 |
|------|------|
| `question` | 位置参数; 命令行模式省略时从 stdin 读入 |
| `--user` | `source_user` 标识, 默认 `cli` |
| `--persona-file` | 从文件读入人格 prompt, 文件名 (无扩展) 作为 `persona_name` |
| `--stream` | 走流式路径 (先加载记忆再流式转发, 结束后异步跑记忆图) |
| `--debug` | 让 Forwarder 打印所有上游 HTTP JSON, 底层设 `MNEMOSYNC_DEBUG=1` |
| `--via-http` | 命令行模式专用; 改走 `http://127.0.0.1:16125/v1/chat/completions` |
| `--api-key` | `--via-http` 时用, 默认读 `MNEMOSYNC_API_KEY` 环境变量 |
| `--base-url` | `--via-http` 目标地址, 默认 `http://127.0.0.1:16125` |
| `--verbose` / `-v` | 打印 DEBUG 级 Python 日志 |

---

## 6. 提示词覆盖管理 `prompt`

`prompt` 子命令用来管理 Agent 提示词的用户覆盖层, 允许在不改代码/不重启的前提下调整 5 个 Agent 与主对话框架的提示词。

**运行时行为**: PromptStore 每次请求都读盘 (无内存缓存), 所以 CLI 修改后**下一次请求立即生效**, 无需重启 `serve`。

**存储位置**:

- 默认层 (随包发布, in git): `src/core/agents/prompts/defaults/*.md`
- 用户覆盖层 (gitignored): `data/prompts/*.md`, 目录可在 `[storage] prompts_override_dir` 配置
- 备份: `data/prompts/.history/<name>-<YYYYMMDD-HHMMSS-NNN>.md`, 每个 name 保留最近 10 份

**注册的提示词** (8 个, 详见 [agents.md §7](agents.md#7-自定义-agent-提示词)):

`memory_analysis`, `memory_analysis_decay_header`, `relationship_analysis`, `prompt_cleaning_system`, `prompt_cleaning_user`, `proxy_thinking`, `sentence_classifier`, `main_dialogue_frame`

### 6.1 子命令

| 子命令 | 说明 |
|--------|------|
| `prompt list` | 表格显示 name / description / overridden / version |
| `prompt show <name>` | 打印当前生效版本到 stdout (含 YAML frontmatter) |
| `prompt show <name> --from-default` | 强制打印默认版本 (忽略覆盖) |
| `prompt set <name> --file <path>` | 从文件读取并保存为覆盖 |
| `cat <path> \| prompt set <name>` | 从管道读取 |
| `prompt set <name> --edit` | 打开 `$EDITOR` (fallback `vi`) 编辑当前生效版本 |
| `prompt set <name> --edit --from-default` | 从默认版本开始编辑 (忽略现有覆盖) |
| `prompt reset <name>` | 删除覆盖 (自动备份最后一版到 `.history/`) |
| `prompt reset --all` | 全部回默认 |
| `prompt validate <name>` | 校验当前覆盖占位符是否齐全 |
| `prompt validate --all` | 校验全部; 有任何错误退出码 2 (CI 友好) |

### 6.2 校验规则

保存前会校验 registry 中声明的占位符是否全部在内容中出现 (格式为 `__NAME__`)。缺失任一占位符 → 拒绝写盘, 已有覆盖不动。

**注意**: 占位符只识别 `__NAME__` (前后各两下划线), 不识别 `{name}` / `{{name}}` 等其它模板语法。这是全项目统一约定, 见 [dev-decisions.md](../dev-decisions.md)。

### 6.3 典型工作流

```bash
# 查看有哪些可覆盖的提示词
mnemosync prompt list

# 把默认版本作为起点写到本地文件
mnemosync prompt show memory_analysis --from-default > my_memory.md

# 编辑后保存
mnemosync prompt set memory_analysis --file my_memory.md

# 或直接用编辑器改
mnemosync prompt set memory_analysis --edit

# 出问题回退
mnemosync prompt reset memory_analysis
```

### 6.4 边界

- CLI 只操作**本地文件**; 若在远端服务器, 通过 SSH 登录后运行 CLI 即可, 不需要 HTTP 客户端
- 面板/WebUI 场景走 REST 接口 (`/panel/admin/prompts`), 见 [auth.md §5](../auth.md#5-角色-数据流) 与 admin 路由代码
- 路径穿越已被 registry 白名单挡住: `prompt show ../etc/passwd` 会返回 "未知的提示词"

---

## 7. 与其他模块

| 模块 | 关系 |
|------|------|
| [认证 API](../auth.md) | 交互式 shell 用 `SqliteAuthStore` 校验管理员账号 |
| [API Key 管理](api-key.md) | 交互式 shell 的 Key 命令通过 `SqliteApiKeyStore` |
| [LLM 服务管理](llm-service.md) | 服务商 + `role_bindings` 命令直接通过 `LLMServiceStore` (v0.2.3 起 SQLite 权威, 不再写 `config.local.toml`) |
| [Forwarder](forward.md) | `test-model` / `ls-models` 通过 Forwarder 发探活请求 |
| [消息处理](message-processing.md) | `ask` 直接进入 LangGraph, 与 HTTP 路径共用实现 |
| [Agent 提示词](agents.md#7-自定义-agent-提示词) | `prompt` 子命令通过 `PromptStore` 读写覆盖层 |

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.0.0 | 2026-03-25 | 初始 CLI 设计草案 |
| v0.2.0 | 2026-07-12 | 顶层命令 `init/serve/stop/login/help`, LLM 服务管理命令进入交互式 shell |
| v0.2.1 | 2026-07-14 | 新增 `ask` 命令与 `--debug` 上游日志 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 补 `upgrade` / `show-config` / `set-embedding-model` / `set-rerank-model`; 移除文档中不存在的 `revoke-key` / `list-users` / `change-password` shell 命令 |
| v0.2.1 | 2026-07-16 | 新增 `prompt` 子命令 (list/show/set/reset/validate), 支持 --file/stdin/--edit |
| v0.2.3 | 2026-07-17 | 模型命令改为 `model {ls,add,rm,reorder,test}` (role_bindings 表, 热更新); 移除 `set-main-model` / `set-assist-model` / `set-embedding-model` / `set-rerank-model` (旧路径通过 config_writer 已废弃) |
| v0.2.4 | 2026-07-17 | `model add` 新增 `--context N` / `--dim N`; 新增 `memory reindex [--prune]` 与 `memory prune [--dry-run]` (走面板 HTTP, 自动换 JWT) |
| v0.2.7 | 2026-07-18 | 新增 `persona reset [--dry-run] [--yes]`: 走 `POST /panel/admin/persona/reset`, 交互式二次确认, 与 `memory reindex` 互斥 |
| v0.2.8 | 2026-07-18 | `model add` 新增 `--send-dim`: 拆分向量库维度锁与上游 `dimensions` 参数, 默认不透传 (兼容 bge/bce/jina 等固定维模型) |
