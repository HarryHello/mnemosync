# 角色卡导入 | Character Card Import

> **模块版本**: v0.3.3
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-28
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 支持导入 SillyTavern V1/V2 格式的角色卡 (PNG 或 JSON), 自动映射到结构化 PersonaDefinition。这让用户可以复用社区角色卡生态, 无需从零编写人格 prompt。

**代码位置**: [src/infra/character_card.py](../../src/infra/character_card.py)

---

## 2. 支持的格式

| 格式 | 来源 | 文件类型 | 说明 |
|------|------|---------|------|
| SillyTavern V1 | 社区角色卡 | PNG/JSON | 传统格式, 元数据嵌在 PNG IEND 之后 |
| SillyTavern V2 | 新版角色卡 | PNG/JSON | spec v2, 元数据在 tEXt chunk 中 (key="chara") |
| JSON | 通用导出 | JSON | 直接包含角色卡字段 |

---

## 3. 解析流程

```
角色卡文件 (PNG / JSON)
  │
  ├─ PNG 文件
  │   ├─ 检测 tEXt chunk (V2 格式, key="chara")
  │   └─ 检测 IEND 后的 JSON payload (V1 格式)
  │       ├─ 带长度前缀
  │       └─ 直接 JSON
  │
  ├─ JSON 文件
  │   └─ json.loads()
  │
  ▼
安全校验 (_sanitize_data)
  ├─ JSON 深度 ≤ 10
  ├─ 字符串长度 ≤ 10000
  ├─ 文件大小 ≤ 10MB
  └─ 丢弃可执行字段 (javascript / code / eval)
  │
  ▼
CharacterCard (标准化访问接口)
  │
  ▼
map_to_persona(card) → PersonaDefinition identity dict
  │
  ▼
create_persona_definition(card) → 完整 PersonaDefinition dict
```

---

## 4. 字段映射

| SillyTavern 字段 | PersonaDefinition 字段 | 说明 |
|-------------------|----------------------|------|
| `name` | `persona_addressing` | 人格自称 |
| `description` | `identity.personality` | 主要身份描述 |
| `personality` | `identity.speaking_style` | 说话风格 |
| `scenario` | `identity.context` (补充) | 场景描述 |
| `first_mes` | (保留为 opening_message) | 开场白 |
| `mes_example` | (保留为示例对话) | 对话示例 |
| `system_prompt` | 附加到 `identity.personality` | 系统约束 |
| `post_history_instructions` | `identity.context` | 后历史指令 |
| `creator_notes` | `identity.values` | 创作者备注 |
| `character_book` | (Lorebook 条目) | 世界书/知识库 |

---

## 5. 安全限制

| 限制 | 值 | 说明 |
|------|-----|------|
| 最大文件大小 | 10 MB | 防止内存耗尽 |
| 最大 JSON 深度 | 10 层 | 防止递归炸弹 |
| 最大字符串长度 | 10,000 字符 | 防止注入 |
| 可执行字段 | 丢弃 | `javascript` / `code` / `eval` |

---

## 6. API

### 6.1 parse_file(file_path) → CharacterCard

从文件路径解析角色卡。支持 PNG 和 JSON。

### 6.2 parse_png(data: bytes) → dict | None

从 PNG 二进制数据中提取 SillyTavern 角色卡元数据。

### 6.3 map_to_persona(card: CharacterCard) → dict

将角色卡字段映射到 PersonaDefinition identity 格式。

### 6.4 create_persona_definition(card: CharacterCard) → dict

从角色卡创建完整的 PersonaDefinition dict (含 `identity`, `space_overrides`, `changelog`)。

---

## 7. 与其他模块

| 模块 | 关系 |
|------|------|
| [结构化人格](persona-definition.md) | 导入结果映射到 PersonaDefinition |
| [人格存储](persona-definition.md#5-存储) | 导入后写入 `persona_versions` 表 |
| [Lorebook](#) | `character_book` 字段可导入为 Lorebook 条目 (未来) |

---

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.3.3 | 2026-07-28 | 初始版本: SillyTavern V1/V2 PNG 解析 + JSON 解析 + 安全校验 + 字段映射 |
