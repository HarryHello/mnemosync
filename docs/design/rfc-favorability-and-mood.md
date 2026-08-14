---
标题: RFC — 好感度 × 情绪系统（人格状态驱动语气）
状态: 📝 草稿（待评审）
日期: 2026-08
作者: HarryHelloo（研讨产出）
关联: docs/modules/agents.md, docs/modules/memory-system.md, docs/modules/langgraph.md
---

# RFC: 好感度 × 情绪系统 — 人格状态驱动语气

## 1. 背景与动机

### 1.1 现状问题（均有代码证据）

1. **信任度是死参数**：关系分析提示词（`relationship_analysis.md` v4）的信号参考只有亲密度条目（称呼/披露/情感/距离），**没有任何信任信号指引**，模型常年输出 `trust_delta = 0.0`。`CONFIDENTIAL_TRUST_THRESHOLD = 0.7`（`audience.py:83`）实际极少触发，机密记忆基本只对来源用户本人可见（自桶放行）。
2. **关系只能升不能降**：`Relationship.apply_delta` 将亲密/信任 `clamp(0, 1)`（`models.py:313-314`），负 delta 语义被阉割为"最多归零"——**没有表达厌恶/敌对的通道**。提示词虽有一条"距离信号 -0.10~-0.20"，但正面信号 4 条、负面 1 条且无示例，LLM 存在"关系越来越好"的社会性偏置。
3. **情绪与主对话语气完全脱钩**：`_compute_emotion` 预计算的情绪只注入关系/记忆分析 Agent（`__EMOTION_ANALYSIS__`），`build_main_dialogue_messages` 无情绪参数——"关系 + 情绪共同决定人格语气"的原设想目前只有关系一个输入。
4. **记忆隔离 ⇒ 全局状态必须显式存在**：对话流水是 per-space / per-user 隔离的（空间锁、受众过滤）。人格在空间 A 被激怒，空间 B 的短期历史里没有这条信息——**情绪无法从上下文推断，必须由服务器显式持久化、全局共享**。这修正了当年删除 Social State 的理由（"短期历史窗口足够"只对 per-space 上下文成立）。

### 1.2 目标

- 将亲密/信任统一为单一**好感度**（允许为负），并让关系分析 Agent 能主动降低好感度
- 引入**全局情绪状态（mood）**：连续量存储、使用时分阶段，驱动主对话语气
- **对象化情绪**（对谁生气）复用记忆系统承载，天然衰减、可审计
- 关系 × 情绪构成**二维矩阵**，以预设提示词工程引导语气（克制逻辑内嵌矩阵）

### 1.3 非目标

- 不实现完整 Social State（当前话题/待答问题/未完成承诺等）——维持已删除决定
- 不引入额外状态表承载对象化情绪（用记忆系统）
- 不做情绪表演的完整模拟（仅语气引导，不改变人格底层价值观）

## 2. 设计一：好感度（Favorability）

### 2.1 定义

- 单一连续标量，内部存储 `float [-1, 1]`（饱和），面板/API 显示映射为 `-100 ~ 100` 整数（galgame 观感）
- 负值表达厌恶/敌对；0 为中性
- 语义：**综合亲近倾向**（旧亲密/信任的压缩）

### 2.2 迁移（一次性，升级兼容）

- 旧系统 trust ≈ 0，`0.9×max + 0.1×min` 公式会退化为 `0.9×intimacy`，**全体存量关系无形打九折**（intimacy=0.5 边界用户跌档）
- **采用 `favor = intimacy`（或 `max(intimacy, trust)` 兜底）**：零缩放、零漂移
- 迁移后删除旧字段（`intimacy_score` / `trust_level`）
- 隐私门控迁移：旧 `CONFIDENTIAL` 判定（trust ≥ 0.7）改映射为 `favor` 阈值（见 §2.4），升级瞬间接受一次性语义漂移并在升级脚本中告警

### 2.3 演进（每轮更新，参数 per-persona 可配置）

不对称更新——**慢热快冷**：

```
favor += (delta ≥ 0 ? α_up : α_down) × delta
clamp 到 [-1, 1]
```

- **α_up / α_down 来自配置文件**（TOML），人格内按预设 id 引用：

```toml
[[relationship_alpha]]
id = "normal"
label = "普通"
alpha_up = 0.2
alpha_down = 0.5

[[relationship_alpha]]
id = "sensitive"
label = "高敏感"
alpha_up = 0.5
alpha_down = 0.8

[[relationship_alpha]]
id = "rational"
label = "理性"
alpha_up = 0.1
alpha_down = 0.3

[[relationship_alpha]]
id = "gullible"
label = "轻信"
alpha_up = 0.5
alpha_down = 0.4

[[relationship_alpha]]
id = "guarded"
label = "戒备"
alpha_up = 0.1
alpha_down = 0.8
```

- 预设结构：`id / label / alpha_up / alpha_down`；**面板不做自定义**——用户直接改配置文件，服务照常读取（预设名/数值的变更即时生效或重启生效，按现有配置加载机制）
- 人格定义（PersonaDefinition）引用预设 id，如 `relationship_alpha = "sensitive"`；未知 id 回退 `normal`
- （预设取值的灵感来源是 MBTI 式性格差异，仅定参时参考，不做 MBTI 组合）
- δ 来源：关系分析 Agent 输出（LLM，见 §3.1 prompt v5）

### 2.4 类型谱系（扩展负侧）

```
hostile(-1.0~-0.5) → cold(-0.5~-0.1) → stranger(-0.1~0.2) → acquaintance(0.2~0.5) → friend(0.5~0.8) → intimate(0.8~1.0)
```

- 原 `stranger → acquaintance → friend → intimate` 阈值（0.2/0.5/0.8）保留；负侧两档：**-0.5 以下为敌对（hostile）**，-0.5~-0.1 为关系变差（cold），-0.1 以上仍属普通关系
- **标签语义与枚举解耦**：`stranger` 档在语义上不是"陌生人"，而是"关系十分普通"——枚举名保留（内部/面板），**提示词层用自然语言描述**
- 注意：`format_relationship`（`context.py:70-72`）目前把"关系类型: {type}（亲密度 0.xx/1.0...）"原文注入主对话 system——**模型实际看得到标签与数值**。改造后主对话只注入阶段化描述（如"与对方关系：普通"），不注入数值；数值仅保留给关系分析 Agent 的 `__CURRENT_REL__`（算 delta 需要精确基线）
- `relationship_type` 降级为"阶段标签投影"：数值推导真相，枚举仅供展示/查询

### 2.5 信任门控现状与设计来源（查证结论）

**设计来源**（v0.1 记忆模型 `memory-model.md` §2.4，已删除文档）：

1. **用户授权披露机制**（原始设计）：用户通过自然语言指令（"不要告诉 A""B 可以知道""这个只有你能知道"）定义细粒度分享规则 → 解析为 `custom_policies`（deny/allow）→ 记忆按策略放行
2. **四级可见性**：`source_restricted → friends_only → confidential → public`，决策矩阵规定 `confidential 且信任度 < 0.8（实现为 0.7）→ 拒绝`——**信任划分本是为"用户允许向他人公开"时服务的**
3. **早期简化**：授权披露未实现，改为**强制私密**（一切默认 `SOURCE_RESTRICTED`，隐私优先），此后未补回

**现状**（`audience.py:82-83`）：整条数据链从未接通——记忆分析 Agent 无 visibility 输出（全默认 source_restricted）、无 CONFIDENTIAL 写入方、trust 无数据源。门控是纯理论分支。

**两条机制的分界（评审定案）——隐私不变量**：

> **用户消息/用户内容绝不因关系阈值放行**。用户内容的可见性只有一条通道：`custom_policies` 授权（deny 一票否决 / allow 白名单，判定逻辑已在 `AudienceFilter._check_policies`）。阈值判断（favor 门槛）**只适用于人格自生产内容**（persona 自己的输出，不包含用户隐私——如未来的人格日程/自生产记忆）。

**结论**：

- 好感度迁移零兼容负担；`CONFIDENTIAL` 重定义为 `favor ≥ 阈值`，但**适用范围 = 人格自生产内容**
- 当前系统**没有自生产内容**（人格日程模拟未实现）——门控保持"未启用"是正确状态，本期只保留语义定义与阈值（friends_only ≥ 0.5、confidential ≥ 0.8，见 §9），待自生产功能落地时启用
- 用户内容（SOURCE_RESTRICTED）永远强制私密，与 favor 无关

### 2.6 影响面

| 位置 | 变更 |
|---|---|
| `models.py Relationship` | 删除 intimacy/trust，新增 favor（-1..1）、类型谱系扩展、apply_delta 放开 clamp |
| `audience.py` | `CONFIDENTIAL_TRUST_THRESHOLD=0.7` → favor 阈值（如 ≥0.8/intimate 档）；`FRIEND_TYPES` 扩为含负侧？不，FRIENDS_ONLY 仍只认 friend/intimate |
| 关系分析 Agent | 输出单 delta（见 §3） |
| 面板 | 双进度条 → 单值显示（-100~100） |
| DB | `relationships` 表迁移（列替换） |

## 3. 设计二：关系分析 Agent 改造

### 3.1 Prompt 重构（version 4 → 5）

- 信号参考改**三段式**：正向 / 负向 / 中性
- 负向信号细化并配示例：羞辱、贬低、欺骗、威胁、冷暴力、攻击人格核心价值观，各给具体示例与幅度区间
- 负向幅度上限放宽（如 `-0.30 ~ -0.50` vs 正向 `+0.05 ~ +0.20`），配合运行时 α_down
- 明确"关系可以恶化"的表述，移除"默认增量为零"的保守倾向（保留不确定时为零）

### 3.2 输出契约

```json
{"signals_detected": [...], "favor_delta": -0.35, "new_relationship_type": "cold", "notes": "...", "reasoning": "..."}
```

- 双 delta → 单 `favor_delta`（可负）
- 调用 `update_addressing` 规则不变

### 3.3 "激怒本条生效"的实现通道（不采用规则词库）

**已否决规则词库方案**（评审结论）：需要大量优质数据、准确性极低。

替代通道——**情绪分析前置（LLM，零额外延迟）**：

- `_compute_emotion` 本就在主对话节点内、主 LLM 之前运行（`_prepare_context`），把结果从"只喂分析 Agent"改为**同时注入主对话（矩阵行）+ 更新全局 mood** → "本条激怒"通过 mood 本条生效，非流式无新增延迟
- `favor` 的更新仍由后置关系分析驱动——关系本就该慢热快冷，接受一条消息的延迟
- LLM 偏正问题交由 §3.1 的 prompt v5（负向信号细化 + 示例）解决，不引入确定性拦截
- 流式路径注意：情绪分析目前缺失（后台图跳过 `_prepare_context`），若要流式也 mood 本条生效，需在流式 handler 前置情绪分析（首 token +1~2s）——见 §9 开放问题

### 3.4 测试

- 负面消息 → favor 下降（单元 + 集成）
- 敌对关系 → 类型进入负侧档
- 无信号 → delta 严格为 0（回归保护）
- 规则命中 → 前置扣减生效、不依赖 LLM

## 4. 设计三：全局 Mood 状态机

### 4.1 定义

- 连续 `valence ∈ [-1, 1]`（好-坏），**全局、per-persona**（`persona_id` 维度），跨空间共享
- **标签只用"情绪好坏"**（评审定案）：单维 valence 无法区分"愤怒/伤心/焦虑"（同为负），**状态标签禁止使用明确情绪名**（如"愤怒"）——阶段投影（使用时）用心情级描述：`心情极差(-1.0~-0.6) / 心情差(-0.6~-0.2) / 心情不佳(-0.2~0) / 心情不错(0~0.2) / 心情好(0.2~0.6) / 心情极好(0.6~1.0)`（6 段已确认）
- **具体情绪（愤怒/伤心等）放 cause**：`_compute_emotion` 的细粒度结果（emotion/category/keywords/summary）写入 `persona_moods.cause`（自然语言，可含明确情绪词）并保留给关系/记忆分析 Agent（`__EMOTION_ANALYSIS__`）；mood 状态本体只取 valence 投影

### 4.2 更新（情绪变化克制）

```
mood_new = 惯性(mood_old) + 衰减 + 本条冲击
惯性: 0.8 × mood_old          # 高惯性——情绪变化不激烈（评审要求）
单步上限: clamp(|Δ|, ≤ 0.3)    # 情绪不会瞬间跨极性跳变（高兴→心情差需多轮累积）
最小阈值: |Δ| < 0.05 不更新    # 防抖动（普通闲聊不扰动 mood）
衰减: -sign(mood) × λ          # 自然消退（几小时到几天回中性）
冲击: α_llm × LLM 情绪分析（valence 投影）  # 前置通道，本条生效
```

- 前置：情绪分析（LLM）结果直接冲击 mood 并注入主对话（矩阵行）——"本条激怒"本条生效，非流式零额外延迟（`_compute_emotion` 本就在主 LLM 前）
- 流式：接受情绪分析前置的 +1~2s TTFT（评审确认可接受）
- **幂等**：mood 更新挂 `interaction_id` 去重，幂等重放不重复冲击

### 4.3 存储（并入 personas 表，评审定案）

- **并入现有 `personas` 表**（当前仅 id/name/description/is_active/created_at/updated_at 六列，`add_column_if_missing` 迁移现成）加三列：`mood_valence` / `mood_cause` / `mood_updated_at`
- 理由：persona 一行一个，mood 天然唯一；读人格时状态随行到达，无 join；SQLite 单行更新成本可接受（仅活跃人格被写）
- 权衡记录：personas 表将承载每轮高频写（与配置类数据的读多写少混合）——若未来状态字段变多（>5 个）再拆 `persona_state` 表；人格导入导出时 mood 属运行时状态，不随行
- **`mood_cause` = 全局心情原因（为什么心情不好，自然语言可含具体情绪）**，与 valence 原子更新
- **cause 是 public 注入文本（评审定案）**：主对话 model 需要知道自己的具体情绪才能表现——cause 注入所有对话的 system prompt
- **写路径卫生规则**：写 cause 的 Agent（情绪/关系后置流程）**不得写入私密内容**——"被谁激怒"不算隐私（可写），但具体内容（对话细节、秘密、健康信息等）不得写入；由写 agent 的 prompt 约束 + 后置内容校验（可选）保证。注入侧不再做受众分叉（cause 本身已是脱敏文本）
- 面板显示当前 mood、强度与原因（对管理员完整可见）

## 5. 设计四：二维矩阵提示词引导

### 5.1 矩阵定义（6×6，可配置，不硬编码）

好感度 6 档 × 情绪 6 段 = 36 格，**每格一段纯文本**（无标签模板结构）：

```text
## hostile_心情极差
（一段自然语言引导，如"你刚被羞辱过，而面前正是那个人。语气冷淡带刺，不掩饰厌烦，但不必歇斯底里。"）
```

- **配置化复用提示词两层存储**：`PROMPT_REGISTRY` 白名单新增 `mood_matrix` 条目（无占位符校验），defaults 文件 + `data/prompts` 用户覆盖层 + 面板提示词页面统一管理（评审确认）
- **覆盖合并**：用户覆盖文件可只写要改的格子，加载时 defaults 36 格打底 + 覆盖合并（区别于现有整体覆盖——这是矩阵文件的特殊需求）
- 缺格/损坏回退默认格；面板可编辑、可校验（标题分格命名合法性）

### 5.2 克制逻辑内嵌

- 同一情绪列（心情差），不同好感度行：对挚友 → "烦躁但亲昵，倾向倾诉"；对陌生人 → "冷淡克制，简短回避"；对仇人 → "带刺不留情面"
- **克制 = 矩阵行差异本身**，无需独立克制机制

### 5.3 注入

- 主对话 system prompt 新增"人格当前状态"段：`[状态: 好感度档(对当前发言者) × 情绪段]` + 对应格子文本
- 不注入连续数值（`format_relationship` 的数值注入一并移除，改阶段描述）

## 6. 设计五：对象化情绪 → 锚点记忆（EPHEMERAL）

**作用域划归（评审定案）**：全局心情原因 → `persona_moods.cause`（§4.3，状态层，方案 C）；对象化情绪 → 记忆系统（关系层，方案 A）。`list_permanent` 等现有加载路径零改动。

### 6.1 形态

- **新 `MemoryType.EPHEMERAL`**（先例：EXPRESSION_STYLE 已是第三类型）；`decay_rate` 映射表新增 `0.95 ≈ 2~3 天` 档
- **衰减即消气**：锚点检索不到 = 情绪消退，无需额外消退机制
- 字段：content（"人格对 A 感到恼火，因为……"）、**新增 `subject_actor_id` 列**（`add_column_if_missing` 迁移，有 001/005 先例）、visibility
- **写路径隔离**：EPHEMERAL 只由情绪/关系后置流程写入，记忆分析 Agent 不产出（prompt 约束 + 写路径守卫）
- 实现代价：过一遍所有 `memory_type ==` 分支（生命周期/衰减/面板统计确认新类型走 NORMAL 路径）

### 6.2 确定性加载（记忆系统升级点）

- 向量检索命中不了（A 说"今天天气不错"与锚点语义不相似）
- 上下文装填时：**当前空间参与者 A 在场 ⇒ 新查询 `list_ephemeral_by_subject(subject)`**（按 subject + 未遗忘 + 未 superseded + 时间序取最新）
- 复用现有记忆装填通道（permanent + retrieved 同路径）

### 6.3 去重

- 同对象新锚点 `supersedes` 旧锚点（复用 `superseded_by` 机制，检索不返回被替代项）

### 6.4 与全局 mood 的分工（评审定案）

| 数据 | 承载 | 更新 | 加载 |
|---|---|---|---|
| 全局 mood（valence） | `persona_moods` 表 | 每轮（幂等） | 始终（内存/状态表） |
| 全局心情原因（cause） | `persona_moods.cause` | 随 mood 原子更新 | 注入分叉（§6.5） |
| 对象化情绪（对 A） | EPHEMERAL 记忆 + subject 列 | 后置分析写，supersedes | A 在场才加载 |

- 方案对比结论（RFC 记录）：新类型 EPHEMERAL（语义/查询精准/机制复用）优于复用 NORMAL+高衰减（条件脆弱、语义藏数值、面板无法区分）；全局心情不塞记忆（状态唯一性、原子性优于记忆行）
- 收益：**"始终加载全局记忆"通道不再需要**——list_permanent 不动

### 6.5 可见性语义（cause public 化后简化）

| 数据 | 注入规则 |
|---|---|
| `mood_cause`（全局心情原因） | **public，注入所有主对话**（已脱敏，见 §4.3 卫生规则） |
| 对象化锚点（对 A 生气） | 按 subject 加载：**A 在场才注入**（细节强化："你刚才惹到我了"）；内容同样遵守卫生规则（不写私密细节） |
| 无归属模式 | 不注入锚点（维持现状守卫）；cause 可注入（脱敏文本） |

- cause 承担"谁惹了人格"的全局事实；锚点承担"对 A 的细节态度"，两者分层不冲突
- 卫生规则是隐私边界：**"被谁激怒"不算隐私，"具体内容"可能私密**——写入端约束优先于注入端过滤

## 7. 数据流整合

```text
用户消息(空间S, 说话者A)
 ├─ 前置(确定性, 本条生效):
 │    ├─ 规则强信号命中 → mood 冲击 + favor(A) 扣减
 │    └─ 读取 favor(A) + mood → 矩阵格(档位) 
 ├─ 主对话上下文装填:
 │    ├─ system: [人格状态段] = 矩阵格模板(基调/倾向/强度系数)
 │    ├─ 锚点记忆: 人格对A的活跃锚点(若有, 按subject确定性加载)
 │    └─ 好感度阶段(对A) + mood 阶段
 ├─ 回复(语气 = 矩阵引导 × 记忆上下文)
 └─ 后置(不阻塞, 幂等挂 interaction_id):
      ├─ LLM 情绪分析 → 校准 mood
      ├─ 关系分析(单 delta) → favor(A) 不对称更新 + 类型投影
      └─ 对象化情绪 → 写锚点记忆(subject=A, 高衰减, supersedes旧锚点)
```

分工：**mood 解决"现在什么心情"（全局，矩阵行）**；**好感度解决"对谁克制多少"（矩阵列）**；**锚点解决"对谁、为什么"（记忆通道）**。

## 8. 演进路径

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 好感度迁移（favor=intimacy、类型谱系、clamp 放开、audience 阈值映射、DB 迁移、面板单值） | 存量关系零漂移；机密可见性语义漂移告警 |
| P1 | 关系 Agent 改造（prompt v5、单 delta、负向示例） + 规则层兜底 | "辱骂→favor 下降"用例通过 |
| P2 | mood 状态机（表、更新、幂等） + 主对话注入 | 面板可见 mood；幂等重放不重复冲击 |
| P3 | 二维矩阵模板 + system 注入 | 每格一个语气基调测试用例 |
| P4 | 锚点记忆（类型/加载/去重/可见性） | "A 在场加载锚点、第三方不见原因"用例通过 |

## 9. 开放问题（待评审确认）

1. ~~好感度档位数~~ **已定**：6 档，矩阵 6×6=36 格（每格一段文本）
2. ~~mood 阶段划分~~ **已定**：6 段"心情"级描述（§4.1 边界值）
3. ~~α 参数~~ **已定**：TOML 配置文件预设（id/label/alpha_up/alpha_down），PersonaDefinition 按 id 引用；面板不做自定义；未知 id 回退 normal
4. ~~mood 存储~~ **已定**：并入 `personas` 表三列（§4.3）
5. ~~信任门控~~ **已定**：CONFIDENTIAL 死代码零迁移；两条机制分界——用户内容只走 `custom_policies` 授权（隐私不变量），favor 阈值只适用于人格自生产内容；**待定阈值**（建议 friends_only ≥ 0.5、confidential ≥ 0.8），门控待自生产功能落地时启用
6. ~~流式 TTFT~~ **已定**：接受 +1~2s，情绪分析前置
7. ~~锚点记忆实现~~ **已定**：新 `EPHEMERAL` 类型 + `subject_actor_id` 列
8. ~~cause 可见性~~ **已定**：cause public 注入 + 写路径卫生规则（§4.3/§6.5）；待定：卫生规则是否加后置内容校验（关键词/长度），还是仅 prompt 约束

## 10. 参考

- `src/core/memory/models.py`（Relationship/MemoryType/decay 映射表）
- `src/core/memory/audience.py`（CONFIDENTIAL_TRUST_THRESHOLD=0.7, FRIEND_TYPES）
- `src/core/agents/prompts/defaults/relationship_analysis.md`（v4 信号参考）
- `src/core/graph/nodes/_relationship_analysis.py` / `_helpers.py`（_compute_emotion）
- 历史: `6b428f8` 删除 Social State（"用户评估不必要, 短期历史窗口足够"）
