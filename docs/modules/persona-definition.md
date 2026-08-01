# 结构化人格定义 | Persona Definition

> **模块版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-28
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

v0.3.3 起, 人格从单段 Markdown 文本演进为**结构化 PersonaDefinition**, 各字段独立可管理、可覆盖、可版本化。

**设计约束**:

- **per-user 字段已移除**: `user_addressing` (对用户的称呼) 和 `context` (关系背景) 是 per-user 级别的, 由 `Relationship` 模型维护, 不在人格级定义中存储
- **人格级不可覆盖字段**: `persona_addressing` (人格自称) 是人格级的, 不支持空间覆盖
- **向后兼容**: 现有单段 prompt 自动通过 `from_legacy()` 转为结构化格式

**代码位置**:

- 数据模型: [src/core/persona/definition.py](../../src/core/persona/definition.py)
- 存储: [src/persistence/persona_store.py](../../src/persistence/persona_store.py) (`data/persona.db`)
- 人格 profile 注册: `personas` 表 (多 profile 共存, v0.3.4)
- 版本历史: `persona_versions` 表 (每个 profile 独立版本链)

---

## 2. 数据模型

### 2.1 PersonaDefinition

```python
@dataclass
class PersonaDefinition:
    version: str                         # semver, "0.0.0" = 遗留迁移
    name: str                            # 人格名
    identity: PersonaIdentity            # 核心身份
    space_overrides: dict[str, PersonaOverride]  # 按 space_id 的覆盖
    author: str | None = None
    created_at: datetime
    updated_at: datetime
```

### 2.2 PersonaIdentity

```python
@dataclass
class PersonaIdentity:
    personality: str = ""                # 性格描述 (自由文本)
    speaking_style: str = ""             # 说话风格 (自由文本)
    values: list[str] = []               # 核心价值观
    persona_addressing: str = "人格"      # 人格自称 (如 "绫音")
```

**注意**: `user_addressing` 和 `context` 已移除 — 它们是 per-user 级别的字段, 由 `Relationship` 模型维护。

### 2.3 PersonaOverride (空间覆盖)

```python
@dataclass
class PersonaOverride:
    speaking_style: str | None = None    # 覆盖说话风格
    personality: str | None = None       # 覆盖性格描述
    scenario: str | None = None          # 覆盖场景描述
```

**可覆盖字段**: 只有 `personality`, `speaking_style`, `scenario` 支持空间覆盖。
**不可覆盖**: `persona_addressing` 为人格级, `values` 为全局。

### 2.4 有效身份解析

```python
def get_identity_for_space(self, space_id: str | None) -> PersonaIdentity:
```

按 `space_id` 应用覆盖: 有覆盖用覆盖, 无覆盖用默认。`values` 和 `persona_addressing` 始终来自默认 identity。

---

## 3. 迁移: 从单段 prompt

```python
PersonaDefinition.from_legacy(name, prompt, persona_addressing="人格")
```

- `personality` = 原 prompt 文本
- `speaking_style` = 空
- `version` = "0.0.0" (标记为遗留)

面板编辑时自动转为结构化版本。

---

## 4. 序列化

### 4.1 to_dict()

返回 JSON 可序列化的 dict, 包含 `version`, `name`, `identity`, `space_overrides`, `author`, `created_at`, `updated_at`。

### 4.2 from_dict(d)

从 dict 反序列化为 `PersonaDefinition`。

### 4.3 to_legacy_prompt()

构建单段 prompt 文本 (供 `__PERSONA_SECTION__` 注入), 只包含人格级字段: 人格设定、说话风格、核心价值。

---

## 5. 存储 (personas + persona_versions)

### 5.1 personas 表 (人格 profile 注册表)

```sql
CREATE TABLE personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,  -- 当前激活的人格
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

### 5.2 persona_versions 表

```sql
CREATE TABLE persona_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id TEXT NOT NULL,            -- 关联到 personas 表
    name TEXT NOT NULL,                  -- 版本名 (人格名快照)
    definition TEXT NOT NULL,            -- JSON serialized PersonaDefinition
    changelog TEXT,
    author TEXT,
    created_at TIMESTAMP NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,   -- 当前激活版本
    FOREIGN KEY (persona_id) REFERENCES personas(id)
);
```

### 5.3 版本规则

1. 每个 profile 当前只有一个激活版本 (`active=1`)
2. 更新时写入新版本, 旧版本 `active=0`
3. 旧版本保留用于回滚, 不物理删除
4. 面板显示版本列表, 支持回滚到任一历史版本

---

## 6. 与其他模块

| 模块 | 关系 |
|------|------|
| [记忆系统](memory-system.md) | `to_legacy_prompt()` 生成 `__PERSONA_SECTION__` 注入主对话 |
| [身份管理](identity.md) | 空间覆盖按 `space_id` 应用; per-user 关系由 `Relationship` 维护 |
| [角色卡导入](character-card.md) | SillyTavern 角色卡映射到 PersonaDefinition |
| [Lorebook](#) | 通过 `lorebook_id` 关联 (未来) |
| [配置](../configuration.md) | 与 `config.local.toml [persona]` 和 `data/persona_override.toml` 的关系 |

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.3.3 | 2026-07-28 | 初始版本: PersonaDefinition + PersonaIdentity + PersonaOverride; from_legacy 迁移; to_legacy_prompt; 按空间覆盖; persona_store |
| v0.3.4 | 2026-07-30 | 移除 per-user 字段 (user_addressing/context); 新增 personas 表多人格 profile; persona_id 关联版本链 |
