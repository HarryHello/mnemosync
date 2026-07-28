# 未来人格架构

> **创建时间**: 2026-07-25
> **最后更新**: 2026-07-28
> **状态**: 远期设计 · 未进入开发
> **边界**: 仅记录当前尚未实现的人格能力；现有身份、记忆、关系和 Agent 行为以 `docs/architecture.md` 与 `docs/modules/` 为准

---

## 1. 文档目的

Mnemosync 已具备单人格、多用户身份、空间事件流、受众过滤、关系分区和多说话者 Prompt 基础。本文不再重复这些当前事实，只定义下一阶段尚未实现的人格能力：

1. 将单段人格 Prompt 演进为可版本化的结构化人格；
2. 区分作者预定义知识（Lorebook）与对话中形成的长期记忆；
3. 支持记忆纠正、冲突与来源追踪；
4. 为辅助 Agent 建立统一运行契约。

群聊拟人化、工具事务、平台动作、Expressor 已实现 (v0.3.3); 并发一致性已通过空间级串行锁解决; Social State 经评估不必要 (短期历史窗口足够覆盖群聊上下文), 不再作为目标。

---

## 2. 非目标

本文不承诺：

- 具体版本号和排期；
- 多人格或人格复制；
- 客户端专用适配；
- 主动唤醒或定时触发；
- Social State / 空间状态摘要 (经评估不必要, 短期历史窗口足够);
- 已实现的身份、受众、关系、工具协议和群聊拟人化模型重构。

---

## 3. 结构化人格定义

### 3.1 目标模型

```text
PersonaDefinition
├── identity
│   ├── name
│   ├── description
│   ├── personality
│   ├── values
│   └── speaking_style
├── scenario
│   ├── default_scenario
│   └── space_overrides
├── examples
│   ├── dialogues[]
│   └── opening_messages[]
├── lorebook
│   └── entries[]
├── behavioral_limits
│   ├── never_do[]
│   ├── privacy_rules[]
│   └── role_boundaries[]
├── social_policies
│   └── per_space[]
└── metadata
    ├── version
    ├── author
    ├── source
    ├── created_at
    └── updated_at
```

### 3.2 不变量

- 服务器人格始终是权威来源；
- 客户端 system 消息不能覆盖人格定义；
- 同一实例仍只有一个人格；
- 空间覆盖只能调整场景和表达倾向，不能创建人格副本；
- 人格变更必须可审计、可回滚；
- 角色卡中的可执行脚本、Jailbreak Prompt 和未审查 system prompt 不能直接启用。

### 3.3 角色卡导入

计划支持 SillyTavern Character Card（PNG/JSON）导入：

```text
角色卡
  → 文件与 JSON 安全校验
  → V1/V2 字段归一化
  → Persona Draft
  → 人工预览和编辑
  → 服务器权威版本
```

安全要求：

- 限制文件大小、像素、JSON 深度和字符串长度；
- 限制 Lorebook 条目数；
- 可执行字段只能作为不可执行元数据保留或直接丢弃；
- 导入内容必须经过人工确认；
- 记录来源、导入器版本和内容哈希。

---

## 4. Lorebook

### 4.1 与长期记忆的边界

| 维度 | Lorebook | 长期记忆 |
|---|---|---|
| 来源 | 作者预定义 | 对话中提取 |
| 内容 | 世界观、设定、固定关系 | 用户事实、偏好和事件 |
| 触发 | 关键词或规则 | 语义检索与受众过滤 |
| 更新 | 人工随人格版本更新 | Agent 提取、衰减和人工纠正 |
| 生命周期 | 跟随人格版本 | 独立衰减和遗忘 |

二者可以共享底层检索设施，但必须保持独立的数据类型、来源和生命周期。

### 4.2 计划能力

- Lorebook 条件触发；
- 作用域与优先级；
- Token 预算；
- 条目冲突检测；
- 人格版本切换时的绑定关系；
- 管理面板预览本轮命中的条目。

---

## 5. 记忆纠正与冲突

当前尚未实现结构化的替代关系。目标模型：

```text
MemoryConflict
├── newer_memory_id
├── older_memory_id
├── relation              supersedes / contradicts / clarifies
├── source_event_ids[]
├── confidence
├── decided_by            agent / user / admin
└── created_at
```

规则：

1. 原始事件和旧记忆不因纠正而物理删除；
2. 新记忆通过 `supersedes` 等关系标记旧版本；
3. 默认检索只返回当前有效版本；
4. 人工纠正优先于模型推断；
5. 所有更新保持主体、受众和来源不变，除非用户明确授权；
6. 冲突检测不得跨受众比较不可见记忆。

这项能力应与用户记忆治理一起设计，避免模型单方面覆盖用户事实。

---

## 6. 人格社交策略

### 6.1 目标模型

```text
SocialPolicy(space_type)
├── participation
│   ├── verbosity
│   ├── initiation
│   └── formality
├── expression
│   ├── emoji_style
│   ├── length_preference
│   └── punctuation_style
├── boundaries
│   ├── mute_topics[]
│   └── require_context
└── tool_preferences
    ├── prefer_lightweight_reaction
    └── max_social_actions
```

约束：

- 平台动作只有客户端提供对应 OpenAI tool 时才存在；
- 不允许用括号动作或 RolePlay 文本模拟平台动作；
- 工具偏好不能绕过 API Key 工具策略和客户端权限；
- 空间策略不能改变人格核心身份与隐私边界。

### 6.2 已实现 (v0.3.3)

以下部分已实现, 不再属于本文范围:

- Expressor 表达改写层 (群聊最终文本口语化改写, 清除动作描写);
- API Key 工具策略 (白名单/黑名单/每轮上限/冷却/全局频率限制);
- 隐私跨模态约束 (工具参数不得包含私有记忆/内部 UUID);
- 平台能力提示与选择性参与指南;
- 表达习惯学习 (EXPRESSION_STYLE 确定性提取 + Expressor 注入)。

### 6.3 仍需实现

上方的 `SocialPolicy(space_type)` 结构化数据模型尚未实现。当前 Expressor 和工具策略是全局配置, 尚无按空间类型 (群聊/私聊) 或按空间实例定制表达倾向的能力。

---

## 7. 辅助 Agent 统一运行契约

> 已形成 RFC: [agent-run-contract.md](../rfcs/agent-run-contract.md)。

### 7.1 AgentSpec

```text
AgentSpec
├── name
├── purpose
├── model_role
├── prompt_version
├── allowed_tools[]
├── timeout
├── max_iterations
├── output_schema
└── privacy_scope
```

### 7.2 AgentRun

```text
AgentRun
├── run_id
├── parent_request_id
├── agent_name
├── input_event_ids[]
├── base_version
├── started_at / finished_at
├── status                 running / ok / failed / timeout / cancelled
├── tool_trace[]
├── usage
├── structured_result
└── error
```

### 7.3 需要解决的问题

- 每个辅助 Agent 的独立超时和取消；
- 父请求结束后的后台任务生命周期；
- 与 Debug Event Bus 的职责分界；
- 过期结果拒绝提交或重新验证；
- 运行记录的持久化与隐私保留周期；
- 客户端工具与内部 Agent 工具严格隔离。

这部分应形成独立 RFC，不与人格结构化一次实现。

---

## 8. 推荐研究顺序

本文不是直接迭代清单。建议在工具协议和群聊事件基础稳定后，按以下顺序研究：

```text
1. 记忆纠正 + 用户记忆治理
2. AgentSpec / AgentRun 运行契约
3. 结构化 PersonaDefinition
4. Character Card 导入
5. Lorebook
6. SocialPolicy 按空间定制
```

优先记忆纠正，是因为用户可纠错能力比人格导入和风格扩展更直接影响数据可信度与隐私。

---

## 9. 必须保持的架构不变量

无论后续采用何种具体实现：

1. 一个实例只有一个服务器权威人格；
2. API Key 证明集成来源，不自动证明最终用户；
3. 先按受众过滤，再将信息交给模型；
4. 核心能力不依赖客户端专用修改；
5. 记忆和状态必须保留来源；
6. 客户端工具只交给 MAIN，内部 Agent 不得获得平台动作权限；
7. 不用 RolePlay 动作文本模拟真实平台动作；
8. 完成实施后，当前事实迁入架构或模块文档，并从本文删除。
