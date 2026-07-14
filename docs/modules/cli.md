# 命令行环境 | CLI

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-25
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 定位

CLI 是 Mnemosync 的日常管理入口: 初始化数据库、启停服务、生成/查看 API Key、维护 LLM 服务商与模型配置、命令行调试主对话。

- 顶层命令: [src/cli/cli.py](../../src/cli/cli.py)
- 交互式 shell: [src/cli/cli_interactive.py](../../src/cli/cli_interactive.py)
- 调试命令 `ask` 实现: [src/cli/ask.py](../../src/cli/ask.py)

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

### 4.5 模型配置 (写入 `config.local.toml`)

| 命令 | 说明 |
|------|------|
| `set-main-model <srv_id> <model>` | 设主对话模型 |
| `set-assist-model <srv_id> <model>` | 设辅助模型 (供记忆/关系/代理思考 Agent) |
| `set-embedding-model <srv_id> <model>` | 设嵌入模型 |
| `set-rerank-model <srv_id> <model>` | 设重排模型 (可选) |
| `test-model <srv_id> <model>` | 探活: 用 Forwarder 发一次最小请求 |

**注意**: 这些命令通过 [config_writer](../../src/core/config_writer.py) 修改 `config.local.toml`。修改嵌入模型需要**清空 ChromaDB** 后重启 (维度可能变化), 详见 [configuration.md](../configuration.md) §7。

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

## 6. 与其他模块

| 模块 | 关系 |
|------|------|
| [认证 API](../auth.md) | 交互式 shell 用 `SqliteAuthStore` 校验管理员账号 |
| [API Key 管理](api-key.md) | 交互式 shell 的 Key 命令通过 `SqliteApiKeyStore` |
| [LLM 服务管理](llm-service.md) | 服务商/模型命令通过 `LLMServiceStore` + `config_writer` |
| [Forwarder](forward.md) | `test-model` / `ls-models` 通过 Forwarder 发探活请求 |
| [消息处理](message-processing.md) | `ask` 直接进入 LangGraph, 与 HTTP 路径共用实现 |

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.0.0 | 2026-03-25 | 初始 CLI 设计草案 |
| v0.2.0 | 2026-07-12 | 顶层命令 `init/serve/stop/login/help`, LLM 服务管理命令进入交互式 shell |
| v0.2.1 | 2026-07-14 | 新增 `ask` 命令与 `--debug` 上游日志 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 补 `upgrade` / `show-config` / `set-embedding-model` / `set-rerank-model`; 移除文档中不存在的 `revoke-key` / `list-users` / `change-password` shell 命令 |
