# 身份管理模块

> **模块版本**: v0.3.0
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-26
> **最后更新**: 2026-07-26
> **作者**: HarryHelloo

---

## 1. 概述

v0.3.0 起 Mnemosync 从"单人格单用户"演进为**单人格多用户**: 同一个人格可以同时服务多个真实用户, 在群聊中识别不同参与者, 并支持把同一人在不同平台的身份归一。

三个正交维度:

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────┐
│    Actor     │────▶│ ActorGroupMembership  │◀────│  UserGroup   │
│ 一个平台账号  │     │       多对多绑定       │     │  一个真实人   │
└──────────────┘     └──────────────────────┘     └──────────────┘
       │                                                    │
       ▼                                                    ▼
  (frontend, external_key)                          effective_user_id
  唯一确定一个 Actor                                 = 记忆与关系的隔离边界
```

- **Actor**: 一个前台应用上的一个可识别账号, 由 `(frontend, external_key)` 唯一确定。例如 AstrBot 上的 QQ 号 `12345`、ChatBox 的固定本地用户。Actor 由系统在处理请求时**按身份策略自动创建**, 不由客户端声明。
- **UserGroup**: 一个真实人。管理员将多个 Actor 绑定到同一个 UserGroup, 表示"QQ 号 12345 和 Discord 号 67890 是同一个人"。
- **effective_user_id**: 记忆与关系的隔离边界。Actor 属于某 UserGroup 时为 `group_id`, 否则为 `actor_id`。同组 Actor 共享记忆与关系。
- **Space**: 会话空间 (群聊 = 一个 space)。群聊对话按 `space_id` 分区成独立事件流; 空间共享记忆只对本空间成员可见。

**关键设计约束**:

1. **服务器侧识别**: 客户端视为不可控黑盒, 身份一律由服务器按 API Key 绑定的策略从请求中提取 (见 [不修改客户端行为](../dev-decisions.md))。
2. **非归属模式**: 无策略或解析失败时不创建 Actor、不读写任何私有记忆, 仍可正常回复。不存在 v0.2.x 的 `"default"` 兜底用户。
3. **受众过滤**: 记忆检索先按受众过滤再交给模型, 不靠 prompt 防泄露。见 [memory-system.md §6](memory-system.md)。

**代码位置**:

- 领域模型: [src/core/identity/models.py](../../src/core/identity/models.py)
- 策略解析器: [src/core/identity/resolver.py](../../src/core/identity/resolver.py)
- 持久化: [src/persistence/identity_store.py](../../src/persistence/identity_store.py) (`data/identity.db`)
- 幂等存储: [src/persistence/idempotency_store.py](../../src/persistence/idempotency_store.py) (`data/idempotency.db`)
- 请求贯通: [src/api/routes/forward.py](../../src/api/routes/forward.py) `_resolve_identity_context`
- 管理端点: [src/api/routes/admin.py](../../src/api/routes/admin.py) `/panel/admin/identity/*`
- CLI: [src/cli/identity_cmd.py](../../src/cli/identity_cmd.py)
- 面板: `ui/src/views/IdentityPage.vue` (身份管理页)

---

## 2. 数据模型

### 2.1 Schema

```sql
-- 参与者: 一个前台应用上的一个账号
CREATE TABLE actors (
    id           TEXT PRIMARY KEY,        -- actor_<hex24>
    external_key TEXT NOT NULL,           -- 平台侧标识 (QQ号 / Discord ID / 用户名)
    frontend     TEXT NOT NULL,           -- 前台应用名 (astrbot / chatbox / web ...)
    display_name TEXT,                    -- 昵称
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TIMESTAMP NOT NULL,
    updated_at   TIMESTAMP NOT NULL,
    UNIQUE(frontend, external_key)
);

-- 用户组: 一个真实人
CREATE TABLE user_groups (
    id         TEXT PRIMARY KEY,          -- group_<hex24>
    name       TEXT,                      -- 显示名 (可选, 如 "张三")
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

-- 绑定 (多对多)
CREATE TABLE actor_group_memberships (
    actor_id   TEXT NOT NULL REFERENCES actors(id),
    group_id   TEXT NOT NULL REFERENCES user_groups(id),
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (actor_id, group_id)
);

-- 身份识别策略
CREATE TABLE identity_strategies (
    id            TEXT PRIMARY KEY,       -- strategy_<hex24>
    name          TEXT NOT NULL,
    strategy_type TEXT NOT NULL,          -- direct | api_key_bound | regex | llm
    config        TEXT NOT NULL DEFAULT '{}',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL,
    updated_at    TIMESTAMP NOT NULL
);

-- 幂等缓存 (独立库 data/idempotency.db)
CREATE TABLE idempotency_keys (
    integration_id    TEXT NOT NULL,      -- api_key.id (一个 Key = 一个接入)
    external_event_id TEXT NOT NULL,      -- 平台侧事件 ID
    event_id          TEXT NOT NULL,      -- 首次响应的 chatcmpl-* id
    response_text     TEXT,
    created_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (integration_id, external_event_id)
);
```

已有表的 v0.3.0 扩充:

| 表 | 新增列 | 用途 |
|----|--------|------|
| `api_keys` | `strategy_id` | 绑定的身份策略 |
| `conversation_turns` | `actor_id`, `space_id`, `external_event_id`, `committed_sequence`, `late_arrival` | 空间事件流 |
| `memory_entries` | `space_id` | 空间共享记忆标记 |

`memory_entries.source_user` 与 `relationships.user_id` 语义不变, 现在存的是 **effective_user_id** (无 schema 变更)。

### 2.2 IdentityContext

`IdentityResolver.resolve()` 的产出, 随请求贯穿整个处理链路:

```python
@dataclass
class IdentityContext:
    actor_id: str | None          # None = 非归属模式
    actor: Actor | None
    frontend: str | None
    external_key: str | None
    display_name: str | None
    space_id: str | None          # 会话空间 (群号等)
    channel_type: str | None      # "direct" | "group" | None
    strategy_name: str | None     # 调试用
    external_event_id: str | None # 平台事件 ID (幂等用)
    effective_user_id: str | None # 记忆/关系隔离边界; None = 非归属
```

---

## 3. 身份识别策略

一个 API Key 对应一个前台接入, 绑定**一个**身份策略。策略定义如何从请求中提取 `(external_key, frontend, display_name, space_id, external_event_id)`。

| 策略 | 适用场景 | config 字段 |
|------|---------|------------|
| `direct` | 客户端正确使用 OpenAI `request.user` 字段 | `frontend` |
| `api_key_bound` | ChatBox 等单用户本地应用, Key 即身份 | `external_key`, `frontend`, `display_name`, `channel_type`, `space_id` |
| `regex` | AstrBot 等把身份信息塞进 prompt 文本的前台 | 见下 |
| `llm` | 身份格式不固定、需要语义理解的前台 | `frontend`, `prompt_template` |
| `plugin` | 复杂平台格式 (群聊快照等), 需要代码级解析 | `plugin_name`; 详见 [identity-plugin.md](identity-plugin.md) |

### 3.1 regex 策略 config

```json
{
  "frontend": "astrbot",
  "actor_pattern": "QQ号[:：]\\s*(\\d+)",
  "name_pattern": "用户名[:：]\\s*(\\S+)",
  "space_pattern": "群号[:：]\\s*(\\d+)",
  "event_id_pattern": "消息ID[:：]\\s*(\\S+)",
  "search_in": "last_user"
}
```

- 各 `*_pattern` 取正则**第 1 个捕获组**; `actor_pattern` 未命中 → 非归属。
- `space_pattern` 命中 → `channel_type = "group"`, 否则 `"direct"`。
- `search_in`: `system` (仅 system 消息) / `last_user` (默认, 最后一条 user 消息) / `all` (拼接所有消息), 限定正则搜索的消息范围。旧值 `system_or_first_user` 仍向后兼容。
- `event_id_pattern` 命中的值进幂等表, 平台重发同一消息时原样重放首次响应。

### 3.2 llm 策略

用 ASSIST 角色模型从对话内容中提取, 要求模型返回:

```json
{"actor_id": "...", "actor_name": "...", "space_id": "...", "event_id": "..."}
```

提取失败 / 模型未配置 → 非归属。

---

## 4. 解析流程

`create_chat_completion` 顶部, 在提示词清洗与上游调用之前:

```
1. _verify_api_key → ApiKey (含 strategy_id)
2. strategy_id 为空或策略停用 → 非归属 (effective_user_id=None)
3. IdentityResolver.resolve() 按策略类型提取身份:
   - find_or_create_actor(frontend, external_key)  ← 首次出现自动建档
   - get_effective_user_id(actor_id)               ← 绑组则为 group_id
4. external_event_id 兜底读 Idempotency-Key 请求头 (可选, 不要求客户端适配)
5. 幂等预检: (api_key.id, external_event_id) 命中 → 重放首次响应, 零 LLM 开销
6. 写入 initial_state: source_user=effective_user_id, actor_id, space_id,
   channel_type, external_event_id, api_key_id → 进入图编排
```

### 4.1 非归属模式

无策略绑定或解析失败时:

- 不创建 Actor
- 不写入任何用户的私有记忆 (memory_analysis / relationship_analysis 节点跳过)
- 不检索私有记忆; 仅 PUBLIC 记忆可见
- 正常生成回复, 对话流水照常记录 (actor_id/space_id 为空)

---

## 5. 空间事件流与幂等

### 5.1 空间分区

群聊是一个 space。`conversation_turns` 按 `space_id` 分区:

- `append()` 在 `space_id` 非空时于**同一事务内**分配 `committed_sequence` (该 space 的 MAX+1, 从 0 起)。
- 事件时间早于空间内最新已提交时间 → `late_arrival = 1` (平台乱序送达标记, 序号仍按提交顺序递增)。
- `list_for_space(space_id, since)` 按 `committed_sequence` 定序读取, 走 `idx_conv_space_seq` 索引。
- 短期记忆装填 (见 [memory-system.md §1.4](memory-system.md)): `space_id` 非空时**只读本空间流水**——群聊上下文不混入其他群/私聊的对话; 为空时退化为全局跨前端流水 (单用户私聊)。
- 私聊/非归属轮次 (`space_id` 为空) 不分配序号, 仍按 `ts` 定序。

### 5.2 幂等

群聊平台网络抖动时会重发同一条消息。不做幂等 = 重复的上游 LLM 调用 + 重复记忆写入。

- 请求处理前查 `idempotency_keys (api_key.id, external_event_id)`:
  - **命中** → 非流式原样返回首次 JSON 响应; 流式把缓存文本拼成标准 SSE 序列 (单内容帧 + stop 帧 + `[DONE]`) 重放。不触发任何 LLM 调用与记忆副作用。
  - **未命中** → 正常处理, 成功后写入记录 (`INSERT OR IGNORE`, 保留首次结果; 失败不写入, 允许重试再生成)。
- `external_event_id` 来源: regex/llm 策略提取, 或客户端主动带的 `Idempotency-Key` 头 (可选)。
- 记录随时间窗清理 (`prune_before`)。

---

## 6. 管理端点

前缀 `/panel/admin/identity` (需管理员登录)。

### 策略

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/identity/strategies` | 列表 |
| POST | `/identity/strategies` | 创建 (201); body: `name`, `strategy_type`, `config` (JSON 字符串) |
| GET | `/identity/strategies/{id}` | 详情 |
| PATCH | `/identity/strategies/{id}` | 更新 `name` / `config` / `is_active` |
| DELETE | `/identity/strategies/{id}` | 删除 (绑定它的 Key 失去身份解析, 进入非归属) |

### 参与者 (只读, 系统自动创建)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/identity/actors` | 列表 (分页) |
| GET | `/identity/actors/{id}` | 详情 |
| GET | `/identity/actors/{id}/groups` | 该 Actor 所属的组 |

### 用户组与绑定

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/identity/groups` | 列表 |
| POST | `/identity/groups` | 创建 (201); body: `name` (可选) |
| GET | `/identity/groups/{id}` | 详情 |
| GET | `/identity/groups/{id}/members` | 成员 Actor 列表 |
| POST | `/identity/actors/{actor_id}/groups/{group_id}` | 绑定 (已存在 → 409) |
| DELETE | `/identity/actors/{actor_id}/groups/{group_id}` | 解绑 (不存在 → 404) |

### 用户自助绑定 (跨平台身份归一)

用户可自行跨平台绑定, 无需管理员操作。双触发模式:

**指令触发** (可靠): 用户发送自定义指令词 (默认"绑定"), 服务端拦截并生成 6 位验证码。另一端发送"绑定 {code}"完成确认。不调 LLM, 零成本。

**自然语言触发** (增强): Mnemosync 注入内部 tool (`initiate_identity_binding` / `confirm_identity_binding`), 模型在对话中自然判断意图并调用。服务端拦截执行, 合成 tool_result, 再调一轮 LLM 生成自然回复。客户端看不到内部 tool_calls。

绑定逻辑: 复用 UserGroup, 把两个 Actor 归到同一组。验证码 5 分钟 TTL, 内存存储。指令词可通过 `runtime.identity_bind_command` / `runtime.identity_bind_confirm_prefix` 自定义。

### 关系端点的 actor 解析

`GET/PUT /admin/relationship` 与 `GET /admin/relationship/audit` 接受 `user_id` 或 `actor_id` 查询参数 (至少一个)。传 `actor_id` 时经 identity_store 解析为 effective_user_id——绑定 UserGroup 的 Actor 查到的是**组关系**, 面板上点任一平台账号都能看到"这个人"的关系状态。

---

## 7. CLI

`mnemosync identity` 命令组, 直连 `data/identity.db` (与 `prompt` 命令同模式, 不走 HTTP):

```bash
# 策略
mnemosync identity strategy list
mnemosync identity strategy create --name "AstrBot QQ" --type regex \
    --frontend astrbot \
    --actor-pattern 'QQ号[:：]\s*(\d+)' \
    --name-pattern '用户名[:：]\s*(\S+)' \
    --space-pattern '群号[:：]\s*(\d+)' \
    --event-id-pattern '消息ID[:：]\s*(\S+)'
mnemosync identity strategy show <id>
mnemosync identity strategy update <id> [--name X] [--config JSON] [--active|--inactive]
mnemosync identity strategy delete <id>

# 参与者 (只读)
mnemosync identity actor list [--frontend X] [--search 关键词]
mnemosync identity actor show <actor_id>     # 含 effective_user_id 与组归属

# 用户组
mnemosync identity group list
mnemosync identity group create --name 张三
mnemosync identity group show <group_id>     # 成员列表

# 跨平台身份归一
mnemosync identity bind <actor_id> <group_id>
mnemosync identity unbind <actor_id> <group_id>
```

`create` 也接受 `--config '<json>'` / `--config-file` 直接传完整配置 (优先于便捷参数)。

---

## 8. 面板

侧边栏「身份管理」(`/identity`), 三个 tab:

- **身份策略**: CRUD + 四种类型的 config 模板骨架 + JSON 校验 + 启停开关
- **参与者**: 列表 + 搜索 + 加入用户组
- **用户组**: 创建 + 成员管理 (添加/移出)

「API Key」页创建 Key 时可绑定身份策略 (默认"不归属"), 列表展示各 Key 绑定的策略。

---

## 9. 与其他模块

| 模块 | 关系 |
|------|------|
| [API Key](api-key.md) | Key 绑定 `strategy_id`; `note` 仍派生 `source_frontend` 元数据 |
| [身份解析插件](identity-plugin.md) | `plugin` 策略类型的插件接口与开发指南 |
| [消息处理](message-processing.md) | 身份解析是请求预处理的第一步, 先于提示词清洗与图编排 |
| [记忆系统](memory-system.md) | effective_user_id 为隔离边界; space_id 参与受众过滤; 幂等保护记忆不被重复写入 |
| [LangGraph 编排](langgraph.md) | AgentState 携带 actor_id / space_id / persona_id / channel_type |
| [上游转发](forward.md) | 内部 tool 注入与拦截; 空间级串行锁; 身份绑定指令触发拦截 |

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.3.0 | 2026-07-26 | 初始版本: 身份模型 + 四策略 + 非归属模式 (Sub-Phase A); 空间事件流 + 幂等 (B); 受众过滤联动 (C); 关系按 Actor 解析 (D); 面板身份管理页 + CLI 命令组 |
| v0.3.1 | 2026-07-27 | 第五种策略类型 `plugin`: 插件接口 + AstrBot 参考实现; 新增 `/identity/plugins` 端点 |
| v0.3.1 | 2026-07-31 | regex 策略 search_in 重构: 新增 `last_user`，废弃 `system_or_first_user` (向后兼容); 插件 `preprocess()` 改为可选; 插件文档 |
| v0.3.4 | 2026-07-28 | 用户自助跨平台绑定: 指令触发 + 内部 tool 自然语言触发; 内部 tool 注册表架构 |
