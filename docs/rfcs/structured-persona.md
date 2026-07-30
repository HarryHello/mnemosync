# 结构化人格定义 | RFC

> **状态**: RFC（征求意见稿）
> **日期**: 2026-07-28
> **作者**: HarryHelloo
> **关联**: [forward.md](../modules/forward.md), [future-persona-architecture.md](../design/future-persona-architecture.md), [configuration.md](../configuration.md)
> **关键词**: PersonaDefinition, 人格版本化, 角色卡导入, Lorebook

---

## 1. 动机

### 1.1 现状

当前人格是**单段 Markdown 文本**，写入 `config.local.toml` 的 `[persona]` 段：

```toml
[persona]
name = "绫音"
prompt = "你叫绫音, 是一个沉默寡言但关心哥哥的高中生。你习惯用短句表达..."
persona_addressing = "绫音"
user_addressing = "哥哥"
context = "你和哥哥住在同一个公寓..."
```

这个设计在 v0.2.x 够用，但到 v0.3.x 已暴露出几个问题：

### 1.2 问题

1. **单段文本不可分治**: 人格定义 = 角色身份 + 说话风格 + 场景描述 + 行为约束 + 社交策略，全部混在一个字符串里。改一个字段就得改整个 prompt，没有局部更新。
2. **不可版本化**: prompt 变更没有版本号、没有变更记录、没有回滚。修改后无法区分"模型变了"和"人格变了"。
3. **不可组合**: 无法复用"高冷"风格跨人格、无法按空间覆盖表达倾向、无法导入外部角色卡。
4. **不可审计**: 人格变更不落 audit log，不知道谁改了什么、改了几次。
5. **角色卡导入无路径**: 用户不能导入 SillyTavern Character Card，社区生态不互通。
6. **Lorebook 缺位**: 没有作者预定义知识（世界观、设定）与对话中形成的长期记忆的分离。

### 1.3 目标

1. 将单段人格 prompt 演进为**结构化 PersonaDefinition**，各字段独立可管理。
2. 支持人格版本化（version + changelog + rollback）。
3. 支持角色卡导入（SillyTavern V1/V2 格式）。
4. 区分 Lorebook（作者知识）和长期记忆（对话提取）。
5. 向后兼容：现有单段 prompt 自动转为结构化格式。

---

## 2. PersonaDefinition

```python
@dataclass
class PersonaDefinition:
    """结构化人格定义."""

    # ── 元数据 ──
    version: str                         # semver, 如 "1.0.0"
    name: str                            # 人格名, 如 "绫音"
    author: str | None                   # 创建者
    description: str | None              # 一句话描述

    # ── 核心身份 ──
    identity: PersonaIdentity
    # ── 场景 ──
    scenario: PersonaScenario
    # ── 示例对话 ──
    examples: PersonaExamples
    # ── 行为边界 ──
    behavioral_limits: PersonaLimits
    # ── 社交策略 ──
    social_policies: SocialPolicies
    # ── 关联 Lorebook ──
    lorebook_id: str | None

    # ── 时间戳 ──
    created_at: datetime
    updated_at: datetime

    @property
    def is_legacy(self) -> bool:
        """是否为单段文本 (v0.2.x 格式)."""
        return self.version == "0.0.0"
```

### 2.1 PersonaIdentity

```python
@dataclass
class PersonaIdentity:
    """人格身份: 谁在说话."""

    personality: str                     # 性格描述 (自由文本)
    values: list[str]                    # 核心价值观 ["重视家人", "不喜欢说谎"]
    speaking_style: str                  # 说话风格描述 (自由文本)
    persona_addressing: str = "人格"      # 第三人称称呼, 如 "绫音"
    user_addressing: str = "用户"         # 对用户的称呼, 如 "哥哥"
    context: str = ""                    # 背景设定
```

### 2.2 PersonaScenario

```python
@dataclass
class PersonaScenario:
    """场景: 当前对话发生的情境."""

    default_scenario: str                # 默认场景描述
    space_overrides: dict[str, str]      # 按 space_id 的场景覆盖 {space_id: scenario_text}
```

### 2.3 PersonaExamples

```python
@dataclass
class PersonaExamples:
    """示例对话: 注入 main_dialogue_frame 的 few-shot."""

    dialogues: list[ExampleDialogue]     # 对话示例
    opening_messages: list[str]          # 开场白 (群聊首次出现时)

@dataclass
class ExampleDialogue:
    role: str
    content: str
    user_name: str | None = None
```

### 2.4 PersonaLimits

```python
@dataclass
class PersonaLimits:
    """行为边界: 不能做什么."""

    never_do: list[str]                  # ["模仿真实人物", "扮演他人角色"]
    privacy_rules: list[str]             # ["不透露用户真实姓名"]
    role_boundaries: list[str]           # ["不提供医疗建议", "不进行角色扮演"]
```

### 2.5 SocialPolicies

见 [future-persona-architecture.md](../design/future-persona-architecture.md) §6。

---

## 3. 版本化

### 3.1 存储

```sql
CREATE TABLE IF NOT EXISTS persona_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,               -- "1.0.0"
    definition TEXT NOT NULL,            -- JSON serialized PersonaDefinition
    changelog TEXT,                     -- "新增说话风格描述; 修复背景设定错误"
    author TEXT,                        -- 修改者
    created_at TIMESTAMP NOT NULL,
    active INTEGER NOT NULL DEFAULT 1   -- 当前激活的版本
);

CREATE INDEX idx_persona_versions_active ON persona_versions(active);
CREATE UNIQUE INDEX idx_persona_versions_version ON persona_versions(version);
```

### 3.2 规则

1. 当前只有一个激活版本（`active=1`），更新时写入新版本，旧版本 `active=0`。
2. 旧版本保留用于回滚，不物理删除。
3. 面板显示版本列表，支持回滚到任一历史版本。
4. 版本号自动递增（`1.0.0` → `1.0.1` 自动补丁 → `1.1.0` 字段变更 → `2.0.0` 重大重构）。
5. 角色卡导入的版本为导入时间戳。

### 3.3 从单段 prompt 迁移

```python
def migrate_legacy_persona(name: str, prompt: str, relation: dict) -> PersonaDefinition:
    """将 v0.2.x 单段 prompt 转为 PersonaDefinition."""
    return PersonaDefinition(
        version="0.0.0",  # 标记为遗留
        name=name,
        identity=PersonaIdentity(
            personality=prompt,
            speaking_style="",
            persona_addressing=relation.get("persona_addressing", "人格"),
            user_addressing=relation.get("user_addressing", "用户"),
            context=relation.get("context", ""),
        ),
        scenario=PersonaScenario(default_scenario=""),
        examples=PersonaExamples(dialogues=[], opening_messages=[]),
        behavioral_limits=PersonaLimits(never_do=[], privacy_rules=[], role_boundaries=[]),
        social_policies=SocialPolicies(),
        lorebook_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
```

面板编辑时自动转为结构化版本。

---

## 4. 角色卡导入

### 4.1 支持的格式

| 格式 | 来源 | 文件类型 | 说明 |
|---|---|---|---|
| SillyTavern V1 | 社区角色卡 | PNG/JSON | 传统格式, 元数据嵌在 PNG 末尾 (tavern_v1) |
| SillyTavern V2 | 新版角色卡 | PNG/JSON | spec v2, 更丰富的字段 |
| Character.AI | CAI 导出 | JSON | 简化的角色定义 |

### 4.2 导入流程

```text
角色卡文件
  → 文件类型检测 (PNG/JSON/CAI)
  → 安全校验 (大小 < 10MB, JSON 深度 < 10, 无可执行代码)
  → 字段归一化到 PersonaDefinition
  → PersonaDraft (暂存, 可预览编辑)
  → 人工确认
  → 写入 persona_versions (active=1)
```

### 4.3 字段映射 (SillyTavern V1/V2 → PersonaDefinition)

| SillyTavern 字段 | PersonaDefinition 字段 |
|---|---|
| `name` | `name` |
| `description` | `identity.personality` |
| `personality` | `identity.speaking_style` |
| `scenario` | `scenario.default_scenario` |
| `first_mes` | `examples.opening_messages[0]` |
| `mes_example` | `examples.dialogues` (解析后) |
| `system_prompt` | 附加到 `identity.personality` 或 `behavioral_limits` |
| `post_history_instructions` | `identity.context` |
| `creator_notes` | `author` + `description` 补充 |
| `character_book` (Lorebook) | 导入为 Lorebook 条目 |

### 4.4 安全要求

- 限制文件大小 (< 10MB), 像素 (PNG 2000x2000), JSON 深度 (< 10), 字符串长度 (< 10000)。
- 可执行字段 (如 `system_prompt` 中的函数调用) 只能作为不可执行元数据保留或丢弃。
- 导入内容必须经过人工确认后才能激活。
- 记录来源 (文件名、哈希、导入时间)。

---

## 5. Lorebook

### 5.1 与长期记忆的边界

| 维度 | Lorebook | 长期记忆 |
|---|---|---|
| 来源 | 作者预定义 | 对话中提取 |
| 内容 | 世界观、设定、固定关系 | 用户事实、偏好和事件 |
| 触发 | 关键词或规则 | 语义检索与受众过滤 |
| 更新 | 人工随人格版本更新 | Agent 提取、衰减和人工纠正 |
| 生命周期 | 跟随人格版本 | 独立衰减和遗忘 |
| 作用域 | 空间或全局 | 用户或空间 |

二者可以共享底层检索设施 (ChromaDB)，但必须保持独立的类型标记和生命周期。

### 5.2 存储

```sql
CREATE TABLE IF NOT EXISTS lorebook_entries (
    id TEXT PRIMARY KEY,
    persona_version_id INTEGER,        -- 所属人格版本
    content TEXT NOT NULL,
    keywords TEXT NOT NULL,            -- 触发关键词, JSON array
    priority INTEGER DEFAULT 0,        -- 冲突时优先级
    space_id TEXT,                     -- 限制空间 (NULL=全局)
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP              -- 可选过期时间
);

CREATE INDEX idx_lorebook_keywords ON lorebook_entries(keywords);
CREATE INDEX idx_lorebook_space ON lorebook_entries(space_id);
```

### 5.3 触发与注入

1. 系统 prompt 装填时，检测当前空间和人格版本。
2. 检索当前命中的 Lorebook 条目（关键词匹配 + 语义检索）。
3. 按优先级排序，取 Top K。
4. 注入 `main_dialogue_frame.md` 的 `__LOREBOK_ENTRIES__` 占位符（新增）。

Lorebook 条目**不衰减**、**不遗忘**、**不由 Agent 提取**。

---

## 6. 实现路径

### 阶段一: PersonaDefinition 数据模型

1. 新建 `src/core/persona/definition.py`，定义所有 dataclass。
2. 新建 `src/persistence/persona_store.py`，`persona_versions` 表的 CRUD。
3. 迁移: `migrate_legacy_persona()` 从当前 config 初始化。
4. 面板: 人格编辑改为结构化表单 (vs 当前单段文本编辑器)。

### 阶段二: 版本化

1. 每次面板保存写入新版本。
2. 版本列表 + 回滚。
3. prompt 构建器从 PersonaDefinition 生成 `main_dialogue_frame`。

### 阶段三: 角色卡导入

1. PNG 解析 + JSON 提取 + 字段映射。
2. PersonaDraft 预览 + 人工确认。
3. 写入 `persona_versions`。

### 阶段四: Lorebook

1. `lorebook_entries` 表 + CRUD。
2. 关键词匹配 + 语义检索。
3. 注入 `main_dialogue_frame`。

---

## 7. 未解决的问题

1. **单人格 vs 多人格**: 当前架构是单人格多用户。结构化定义后是否支持多人格？当前设计只负责"一个人格的结构化"，不改变单人格约束。多人格需要单独的 RFC。
2. **Prompt 构建器**: 从 PersonaDefinition 生成 `main_dialogue_frame` 的方式。是拼接为一个字符串（当前模式），还是动态按字段注入？推荐动态注入，这样各字段可以独立覆盖。
3. **与 SpaceState 的关系**: Lorebook 的 `space_id` 字段允许按空间定制知识。这与已放弃的 SpaceState 不同——Lorebook 是作者预定义知识，不是对话状态摘要。
4. **角色卡导入后的版权**: 导入社区角色卡时，来源和作者信息必须保留。人格商店（导入/导出）功能不在本轮范围内。
5. **Lorebook 关键词匹配的语言**: 中英文混合群聊中关键词匹配的准确率。可能需要额外支持同义词和模糊匹配。

---

## 8. 不纳入范围

- 多人格架构（需要独立 RFC）。
- 人格商店（导出/共享）。
- AI 生成人格（从用户描述自动生成 PersonaDefinition）。
- 人格版本之间的自动 diff/merge。
