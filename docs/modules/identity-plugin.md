# 身份解析插件

> **模块版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-31
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

插件是身份识别策略的第五种类型 (`plugin`)，用于正则/LLM 难以覆盖的复杂平台格式——典型场景是群聊快照：平台把多个人的发言拼成一条消息发过来，需要拆分成逐说话者事件。

**与 regex/llm 策略的区别**:

| 维度 | regex / llm | plugin |
|------|------------|--------|
| 输入 | 单条消息文本 | 完整 messages 列表 |
| 输出 | 身份字段 | 身份 + 可选的消息预处理 |
| 配置 | 正则/prompt 模板 | 代码逻辑，config 自定义 |
| 适用 | 格式固定、可模式匹配 | 格式复杂、需要上下文感知 |

**代码位置**:

- 插件接口: [src/core/identity/plugin.py](../../src/core/identity/plugin.py)
- 插件发现: [src/core/identity/plugin_registry.py](../../src/core/identity/plugin_registry.py)
- 插件目录: [plugins/](../../plugins/) (项目根目录)
- 参考实现: [plugins/astrbot.py](../../plugins/astrbot.py)
- 请求集成: [src/api/routes/forward/__init__.py](../../src/api/routes/forward/__init__.py)

---

## 2. 接口

### 2.1 IdentityPlugin (抽象基类)

```python
class IdentityPlugin(ABC):
    name: str = ""          # 唯一标识，用于策略 config 中引用
    description: str = ""   # 人类可读描述，显示在管理面板

    @abstractmethod
    async def extract(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: SqliteIdentityStore,
    ) -> PluginResult | None:
        """从原始消息中提取当前请求者身份。必须实现。"""
        ...

    async def preprocess(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: SqliteIdentityStore,
        identity: IdentityContext,
    ) -> PluginPreprocessResult:
        """生成模型消息与逐说话者规范化事件。可选覆写。"""
        return PluginPreprocessResult(model_messages=messages, events=[])
```

- `extract()` **必须实现**：从消息中识别"当前是谁在说话"，返回 `PluginResult` 或 `None`（识别失败 → 非归属模式）
- `preprocess()` **可选覆写**：默认实现直接返回原消息 + 空事件列表。需要消息预处理（如群聊快照拆分）的平台应覆写此方法

### 2.2 PluginResult

`extract()` 的返回值，描述当前请求者的身份：

```python
@dataclass
class PluginResult:
    external_key: str                    # 平台侧用户标识 (必填)
    display_name: str | None = None      # 昵称
    space_id: str | None = None          # 会话空间 ID (群聊时为群号等)
    channel_type: str | None = None      # "direct" | "group"
    external_event_id: str | None = None # 平台事件 ID (幂等用)
    metadata: dict[str, Any] = {}        # 自定义元数据
```

`external_key` 是唯一必填字段。系统会自动调用 `store.find_or_create_actor(external_key, frontend, display_name)` 建档。

### 2.3 PluginPreprocessResult

`preprocess()` 的返回值：

```python
@dataclass
class PluginPreprocessResult:
    model_messages: list[dict[str, Any]]  # 发给主模型的消息列表
    events: list[NormalizedEvent]         # 逐说话者规范化事件

    @property
    def current_event(self) -> NormalizedEvent | None:
        """返回本轮真实新事件 (origin == "current")。"""
        ...
```

- `model_messages`：清洗后发给主模型的消息。可以是原始 messages 的子集/变换
- `events`：结构化事件列表，分两种 origin：
  - `"history_snapshot"`：群聊快照中的历史发言，**立即写入** conversation_store
  - `"current"`：当前请求者的发言，**本轮成功后**写入

### 2.4 NormalizedEvent

```python
@dataclass
class NormalizedEvent:
    role: str                              # "user" | "assistant"
    content: str                           # 消息内容
    source_frontend: str                   # 来源平台
    origin: str                            # "current" | "history_snapshot"
    source_timestamp: datetime | None      # 消息时间
    actor_id: str | None                   # Actor ID
    effective_user_id: str | None          # 有效用户 ID
    display_name: str | None               # 昵称
    external_key: str | None               # 平台标识
    space_id: str | None                   # 空间 ID
    external_event_id: str | None          # 平台事件 ID
```

---

## 3. 编写插件

### 3.1 最简插件

只需要身份提取、不需要消息预处理时，只需实现 `extract()`：

```python
# plugins/my_platform.py

from src.core.identity.plugin import IdentityPlugin, PluginResult
from typing import Any

class MyPlatformPlugin(IdentityPlugin):
    name = "my_platform"
    description = "我的平台适配器"

    async def extract(self, messages, config, store):
        # 从最后一条 user 消息中解析身份
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            # 你的解析逻辑...
            user_id = parse_user_id(content)
            if user_id:
                return PluginResult(
                    external_key=user_id,
                    display_name=parse_nickname(content),
                )
        return None
```

放入 `plugins/` 目录，重启即生效。

### 3.2 带消息预处理的插件

群聊场景通常需要覆写 `preprocess()`：

```python
class GroupChatPlugin(IdentityPlugin):
    name = "group_chat"
    description = "群聊快照适配器"

    async def extract(self, messages, config, store):
        # 识别当前发言人
        ...

    async def preprocess(self, messages, config, store, identity):
        events = []

        # 1. 拆分群聊快照为逐说话者事件
        for speaker, text in parse_group_snapshot(messages):
            actor = await store.find_or_create_actor(
                speaker.id, self.name, speaker.name,
            )
            events.append(NormalizedEvent(
                role="user",
                content=text,
                source_frontend=self.name,
                origin="history_snapshot",
                source_timestamp=speaker.time,
                actor_id=actor.id,
                effective_user_id=await store.get_effective_user_id(actor.id),
                display_name=speaker.name,
                external_key=speaker.id,
                space_id=identity.space_id,
            ))

        # 2. 当前消息
        events.append(NormalizedEvent(
            role="user",
            content=get_current_text(messages),
            source_frontend=self.name,
            origin="current",
            actor_id=identity.actor_id,
            effective_user_id=identity.effective_user_id,
            display_name=identity.display_name,
            external_key=identity.external_key,
            space_id=identity.space_id,
        ))

        # 3. 编译模型消息
        model_messages = compile_for_model(messages, events)

        return PluginPreprocessResult(
            model_messages=model_messages,
            events=events,
        )
```

### 3.3 config 访问

策略的 `config` JSON 会原样传给 `extract()` 和 `preprocess()` 的 `config` 参数。可以用来传递平台特定配置：

```json
{
  "plugin_name": "my_platform",
  "space_id": "default_group",
  "timezone": "Asia/Shanghai"
}
```

```python
async def extract(self, messages, config, store):
    tz = config.get("timezone", "UTC")
    default_space = config.get("space_id")
    ...
```

---

## 4. 插件发现

### 4.1 机制

应用启动时扫描 `plugins/` 目录：

```
plugins/
  astrbot.py          # 单文件插件
  my_adapter/
    __init__.py       # 子目录插件
  _disabled.py        # 下划线开头，跳过
```

发现流程：
1. 收集 `*.py` 文件（`_` 开头跳过）和含 `__init__.py` 的子目录
2. 动态导入，扫描所有 `IdentityPlugin` 子类
3. 实例化，通过 `name` 属性注册
4. 同名插件后者覆盖（有警告日志）
5. 存入 `app.state.identity_plugins`

### 4.2 注册到策略

在管理面板创建 `plugin` 类型的策略，config 中指定 `plugin_name`：

```json
{
  "plugin_name": "astrbot"
}
```

策略绑定到 API Key 后，该 Key 的请求走插件逻辑。

### 4.3 管理端点

```
GET /panel/admin/identity/plugins
```

返回已发现的插件列表：

```json
{
  "items": [
    {"name": "astrbot", "description": "AstrBot QQ 适配器 — 逐说话者解析群聊快照并编译模型上下文"}
  ],
  "total": 1
}
```

---

## 5. 请求处理流程

```
请求到达 (Authorization: Bearer sk-xxx)
  │
  ├─ API Key 验证 → strategy_id → strategy_type == "plugin"
  │
  ├─ IdentityResolver._resolve_plugin()
  │     ├─ 按 plugin_name 查找插件
  │     ├─ 调用 plugin.extract(messages, config, store)
  │     │     └─ 返回 PluginResult → 创建/查找 Actor → IdentityContext
  │     └─ extract() 返回 None → 非归属模式
  │
  ├─ plugin.preprocess(messages, config, store, identity)
  │     ├─ model_messages → 替换原始消息列表
  │     ├─ history_snapshot 事件 → 立即写入 conversation_store
  │     └─ current 事件 → 本轮成功后写入
  │
  ├─ external_event_id 幂等预检 (命中 → 重放缓存响应)
  │
  └─ 主模型调用 → 响应
```

---

## 6. 参考实现: AstrBot

[AstrBot](https://github.com/Soulter/AstrBot) 是一个 QQ 机器人框架，它在发给 AI 的消息中嵌入 `<system_reminder>` 块来传递用户身份和群聊上下文。

### 6.1 输入格式

AstrBot 的最后一条 user 消息 content 是一个数组（forward pipeline 会预处理为纯文本）：

```
起床了
<system_reminder>User ID: 486394990, Nickname: 小明
Group name: 测试群
Current datetime: 2026-07-27 10:46 (CST)</system_reminder>
<system_reminder>You are in a group chat. Belows are group chat context:
--- BEGIN CONTEXT---
[小红/10:45:01]: 早上好
[小明/10:46:16]: 早上好
--- END CONTEXT ---
</system_reminder>
```

### 6.2 extract() 做了什么

从 `<system_reminder>` 块中正则提取 User ID、Nickname、Group name，返回 `PluginResult`。

### 6.3 preprocess() 做了什么

1. **拆群聊快照**：把 `--- BEGIN CONTEXT ---` 里的 `[昵称/时间]: 内容` 拆成独立的 `NormalizedEvent`，每个说话者一个事件
2. **解析历史发言人**：优先按 QQ 号（`昵称(QQ号)` 格式）查找 Actor，否则按昵称精确匹配
3. **时间排序**：结合 `Current datetime` 和消息内的时间戳，处理跨午夜等边界情况
4. **清洗当前消息**：去掉 `<system_reminder>` 和 context 块，只保留用户实际输入
5. **编译模型消息**：当前消息包装成 `<current_speaker identity="昵称 | QQ 123456">` 标签

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.3.1 | 2026-07-27 | 初始版本: 插件接口 + AstrBot 参考实现 |
| v0.3.1 | 2026-07-31 | `preprocess()` 从 `@abstractmethod` 改为可选（默认返回原消息 + 空事件），降低简单场景接入门槛 |
