# 配置文档 | Configuration

> **系统版本**: v0.3.0
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-24
> **最后更新**: 2026-07-26
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 用**单一配置源**: 项目根目录下的 `config.local.toml`。**文件缺失时用全默认值启动** (见 [config.py:131](../src/core/config.py#L131))——v0.2.3 起模型绑定已从配置文件迁到 `role_bindings` 表, 因此配置文件不再是启动必需。

**辅助入口**:
- 首次部署: 复制 `config.example.toml` → `config.local.toml`, 填入 `[persona]` 与自定义参数
- 环境变量仅接管极少数运行时项 (见 [§4](#4-环境变量-有限支持))
- **模型服务商与角色绑定**不再从本文件读取; 通过面板 (`/panel/admin/model-bindings`) 或 CLI (`mnemosync login` → `model add <role> <service_id> <model>`) 管理, 落库到 `data/llm_service.db` (`role_bindings` + `services` 两张表)

---

## 2. 配置文件结构

真实定义见 [src/core/config.py](../src/core/config.py):

| 段 | 必需 | 说明 |
|----|------|------|
| `[persona]` | 否 (强烈建议) | 服务器人格 (v0.2.1); 缺省使用内置助手人格 |
| `[storage]` | 否 | 数据库/向量库路径 (v0.2.6 新增 conversation_db_path / short_term_days) |
| `[memory]` | 否 | 长期记忆参数 |
| `[graph]` | 否 | LangGraph 编排 + 代理推理开关 |
| `[runtime]` | 否 | HOST/PORT/log_level |

> ⚠️ v0.2.3 之前存在的 `[chat]` / `[embedding]` / `[rerank]` 段**已废弃**, 迁到面板 `模型管理` 页统一维护。旧配置文件里保留这些段不影响启动 (会被忽略), 但 CLI 不再读它们。

---

## 3. 各段字段

### 3.1 [persona] (v0.2.1 服务器优先人格)

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | str | `"纱雾"` | 人格名 (仅面板展示 / prompt 变量) |
| `prompt` | str | 以和泉纱雾为原型的 "宅家内向的妹妹" 长提示 (见 [src/core/config.py](../src/core/config.py) `PersonaConfig`) | 系统提示词, 会拼进主对话 system |

```toml
[persona]
name = "Alice"
prompt = "你是 Alice, 27 岁, 语气温和, 记得用户告诉你的每一件事。"
```

**默认人格**: v0.2.9 起默认人格改为 "宅家内向的妹妹" (以和泉纱雾为原型), 用户身份为哥哥. 想换回中立助手风格请自行覆盖 `[persona]` 段.

**设计原则**: 人格由服务器权威定义, 客户端 system 消息中的角色扮演会被 `prompt_cleaning` Agent 剥离, 只保留功能性指令 (格式要求、工具约束等)。见 [dev-decisions.md](dev-decisions.md) v0.2.1 相关章节。

**v0.2.11 面板覆盖层**: 面板 `设置 → 人格` 页 (`PUT /panel/admin/persona`) 会把编辑结果写入 `data/persona_override.toml`, 优先级 **override > config.local.toml [persona] > 资源默认值** (见 [§6 加载流程](#6-加载流程))。`data/persona_override.toml` 由代码机械生成 (每次 PUT 全量覆写), 结构固定:

```toml
name = "..."
prompt = """
...
"""

[relation]
persona_addressing = "..."
user_addressing    = "..."
context            = "..."
```

- 该文件默认存在 `.gitignore` 中, **不要手编**——手写的注释与格式会被下次 PUT 覆盖
- 面板 "重置为默认" 触发 `DELETE /panel/admin/persona`, 删除本文件, 回退到上一级 (`config.local.toml` 或资源默认)
- `[persona.relation]` 三字段是**新装基线**; 对话中 Agent 通过 [`update_addressing` 工具](modules/tools.md#5-make_update_addressing_toolmemory_store-persona_id-user_id-v0210) 落库到 `relationships` 表的相同字段, 表中非空值会**运行时覆盖** override 值 (即 `relationships > persona_override > config.local > 资源默认`)
- 见 [modules/memory-system.md](modules/memory-system.md) 关系称呼演化章节

### 3.2 [storage]

| 字段 | 默认 | 说明 |
|------|------|------|
| `memory_db_path` | `data/memory.db` | 长期记忆元数据 |
| `llm_db_path` | `data/llm_service.db` | LLM 服务商 + role_bindings |
| `auth_db_path` | `data/auth.db` | 管理员账号 |
| `chroma_dir` | `data/chroma` | ChromaDB 持久化目录 |
| `prompts_override_dir` | `data/prompts` | Agent 提示词用户覆盖层 (gitignore); 默认层在 `src/core/agents/prompts/defaults/` 随包发布, 见 [modules/agents.md §7](modules/agents.md#7-自定义-agent-提示词) |
| `conversation_db_path` | `data/conversation.db` | v0.2.6: 跨前端对话流水 (`conversation_turns` 表) |
| `short_term_days` | `7` | v0.2.6: 短期记忆时间窗宽度, 后台清理任务的删除阈值 |

路径相对项目根目录。API Key 数据库路径当前**在代码中硬编码**为 `data/api_keys.db` ([lifespan.py](../src/api/lifespan.py)), v0.2.x 暂未纳入 storage 段。v0.3.0 新增的 `data/identity.db` (身份四表) 与 `data/idempotency.db` (幂等重放缓存) 同样在 lifespan 中硬编码, 暂不可配。

**注意**: `short_term_days` 是硬边界 — 超过此时长的对话流水会被 lifespan 起的每 24h 后台任务清理; 主装填路径也不再考虑窗外的记录。想扩大保留可以调大此值, 但要注意 SQLite 表体积增长与向量记忆的分工。v0.3.0 起群聊按 `space_id` 分区装填 (时间窗同样适用于空间流水), 详见 [modules/identity.md §5](modules/identity.md#5-空间事件流与幂等)。

### 3.3 [memory]

| 字段 | 默认 | 说明 |
|------|------|------|
| `permanent_limit` | 15 | 永久记忆条数上限 |
| `permanent_load_top` | 7 | 主对话每次加载的永久记忆条数 |
| `retrieval_top_k` | 5 | 语义检索返回条数 |
| `decay_batch_size` | 50 | 衰减评估的批次大小 |

### 3.4 [graph]

| 字段 | 默认 | 说明 |
|------|------|------|
| `checkpoint_backend` | `memory` | LangGraph checkpoint 后端 (`memory` / `sqlite`); v0.2.6 起 checkpoint 仅作单请求内节点共享 state 用, 不再承担跨请求短期记忆 |
| `proxy_thinking_default` | false | 代理推理的**兜底**开关: 请求无 `reasoning_effort` 等提示、主模型也没原生推理时, 是否强制启用 |
| `proxy_thinking_native_reasoning_models` | 见下 | 视为具备原生推理的模型前缀白名单 (命中即 skip 代理推理) |

**默认前缀白名单**:
```
["o1*", "o3*", "o4*",
 "deepseek-r1*", "deepseek-reasoner*",
 "qwen3-*-thinking", "qwq*",
 "gpt-5-thinking-*"]
```
末尾 `*` 通配。除静态前缀外, 流式路径会自适应观察: 上游 chunk 出现 `reasoning_content` 字段 → 该模型加入进程内 `_native_cache`, 下次自动跳过 (重启清空)。

**代理推理决策规则**: 由 [src/api/reasoning_control.py](../src/api/reasoning_control.py) 的 `should_use_proxy_thinking()` 判定, 优先级 tools → 原生识别 → 前台点名推理 → `proxy_thinking_default`。详见 [agents.md](modules/agents.md)。

### 3.5 [runtime]

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` | `0.0.0.0` | 监听地址 |
| `port` | 16125 | 监听端口 |
| `log_level` | `info` | 日志级别 |

---

## 4. 环境变量 (有限支持)

绝大多数配置只从 `config.local.toml` 读取。当前从环境变量读取的项:

| 变量 | 作用 |
|------|------|
| `HOST` / `PORT` | CLI `serve` 命令行标志覆盖 [runtime] |
| `MNEMOSYNC_DEBUG=1` | 打开 Forwarder 的上游请求/响应日志 + http_logs.db 落库 |

无 `.env` 支持——不要指望通过 `.env` 或 `MEMORY_DB_PATH` 之类的环境变量覆盖 config 段。

---

## 5. 完整示例

```toml
# ---- 服务器人格 ----
[persona]
name = "Alice"
prompt = "你是 Alice, 记得用户告诉你的每一件事, 语气温柔。"

# ---- 存储 ----
[storage]
memory_db_path       = "data/memory.db"
conversation_db_path = "data/conversation.db"    # v0.2.6
short_term_days      = 7                          # v0.2.6
chroma_dir           = "data/chroma"

# ---- 记忆 ----
[memory]
permanent_limit    = 15
permanent_load_top = 7
retrieval_top_k    = 5

# ---- Graph ----
[graph]
checkpoint_backend = "memory"
proxy_thinking_default = false
proxy_thinking_native_reasoning_models = [
  "o1*", "o3*", "o4*",
  "deepseek-r1*", "deepseek-reasoner*",
  "qwen3-*-thinking", "qwq*",
  "gpt-5-thinking-*",
]

# ---- 运行时 ----
[runtime]
host = "0.0.0.0"
port = 16125
log_level = "info"
```

模型服务商 (base_url / api_key / main_model / assist_model / embedding / rerank) **不再在这里配**, 到面板 `模型管理` 页面添加, 或用 CLI:

```bash
mnemosync login
> model add main dashscope qwen-max
> model add assist dashscope qwen-turbo
> model add embedding dashscope text-embedding-v3 --dim 1024
> model add rerank dashscope qwen3-rerank
```

---

## 6. 加载流程

1. `get_settings()` 首次调用触发 `load_settings()`
2. 检查 `config.local.toml` 存在性; **不存在时直接返回 `Settings()` 全默认值** (v0.2.3 起放宽, 不再报错)
3. 存在时解析各段, 缺省字段用 `@dataclass` 默认值填充
4. **v0.2.11**: 检查 `data/persona_override.toml`, 若存在则用其覆盖 `[persona]` 段字段 (name / prompt / relation.*, 嵌套字段按键合并), 未覆盖字段沿用上一级
5. 缓存单例, 之后调用返回同一实例
6. 测试可用 `_reset_settings()` 清缓存

对话运行时的 `persona.relation.*` 还会被 `relationships` 表的非空列进一步覆盖 (Agent 通过 `update_addressing` tool 写入), 见 [modules/memory-system.md](modules/memory-system.md) 关系称呼演化。

---

## 7. 更换嵌入模型的完整步骤 (v0.2.4+)

嵌入模型换模型会使已存向量失效, 必须走 Reindex:

1. 面板 `模型管理` → 嵌入卡片 → 点"替换", 填新的 service_id / model / embedding_dim
2. 弹出对话框显示 "N 条记忆的向量将被作废", 选 **"替换并重建"**
3. 面板自动打开 Reindex 抽屉, 后台 `Reindexer.run()` 遍历全部 memory 逐条重新 embed
4. 期间新写入被拒 (`MemoryLifecycle.remember` 会 warn 并跳过), 检索也会 fail (`vector_search` 因 collection metadata 锁定不一致而抛)
5. 完成后 collection metadata 重新锁定新模型, 服务恢复

强制要求 Reindex 是 v0.2.4 决策 (见 [dev-decisions.md](dev-decisions.md))。**不允许**只改绑定不重建 —— 那会让语义空间不同的向量混在同一 collection, 检索质量默默劣化。

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-12 | 从环境变量为主改为 `config.local.toml` 单一配置源 |
| v0.2.1 | 2026-07-15 | 移除虚构环境变量表, 嵌入维度由模型决定, 代理思考启用方式修正 |
| v0.2.1 | 2026-07-16 | `[storage]` 新增 `prompts_override_dir` |
| v0.2.3 | 2026-07-17 | `[chat]`/`[embedding]`/`[rerank]` 废弃, 迁到 `role_bindings`; `config.local.toml` 缺失时不再报错 |
| v0.2.6 | 2026-07-18 | 新增 `[persona]` 段说明; `[storage]` 增 `conversation_db_path` / `short_term_days`; 迁移嵌入模型的完整步骤章节 |
| v0.2.9 | 2026-07-18 | `[persona]` 加 `[persona.relation]` 段 (persona_addressing / user_addressing / context), 默认人格改为"宅家内向的妹妹" |
| v0.2.10 | 2026-07-19 | `[persona.relation]` 三字段变为**运行时可演化** — Agent 通过 `update_addressing` tool 落库到 `relationships` 表, TOML 值降级为"新装基线" |
| v0.2.11 | 2026-07-19 | 新增 `data/persona_override.toml` 覆盖层 (面板 `PUT /panel/admin/persona` 写入, 优先级高于 `config.local.toml`) |
| v0.3.0 | 2026-07-26 | 注明 `data/identity.db` / `data/idempotency.db` 两个新库 (lifespan 硬编码, 暂不可配); 短期记忆时间窗说明补充空间分区装填 |
