# 多 Agent 设计 | Multi-Agent Design

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-07-11
> **最后更新**: 2026-07-16
> **作者**: HarryHelloo

---

## 1. 概述

Mnemosync 一次请求由 LangGraph + API 层编排 **5 个 Agent** 完成。其中代理推理是**原生推理的补齐** (详见 §4), 提示词清洗是**服务器人格权威的守门员** (详见 §6)。

### 1.1 Agent 全景

| # | Agent | 推理方法 | 使用模型 | 触发时机 | 输出 |
|---|-------|---------|---------|---------|------|
| 1 | 主对话 | 直接推理 | 主模型 | 每次请求必跑 | 回复文本 |
| 2 | 代理推理 | CoT (无工具) | 辅助模型 | 主模型无原生推理 & (前台点名推理 或 `proxy_thinking_default=true`) 时启用 | 供主对话参考的思考文本 + 前台 `reasoning_content` 字段 |
| 3 | 记忆分析 | ReAct | 辅助模型 | 主对话后, 与关系分析并行 | 新记忆候选 + 衰减评估 JSON |
| 4 | 关系分析 | ReAct | 辅助模型 | 主对话后, 与记忆分析并行 | 亲密度/信任度增量 JSON |
| 5 | 提示词清洗 | ReAct | 辅助模型 | API 层预处理, 客户端 system 消息非空时 | 保留的功能性指令 + 丢弃的人格描述 JSON |

**代码位置**: 所有 Agent 的执行函数集中在 [src/core/agents/factory.py](../../src/core/agents/factory.py); ReAct 循环由 [src/core/agents/base.py](../../src/core/agents/base.py) 的 `run_react_loop` 驱动。

### 1.2 LangGraph 拓扑

```
[API 层预处理: 服务器人格加载 + 提示词清洗 Agent (可选)]
      │
      ▼
parse_request
      │
      ├─ proxy_thinking_enabled? ──► proxy_thinking
      │                                   │
      └───────────────────────────────► main_dialogue
                                            │
                              ┌─────────────┴─────────────┐
                              ▼                           ▼
                    relationship_analysis          memory_analysis
                              │                           │
                              └─────────────┬─────────────┘
                                            ▼
                                           END
```

**要点**:
- `parse_request` 不是 Agent, 是纯 Python 预处理节点 (提取新消息 + 用户标识)
- **提示词清洗 Agent 不在图中**: 在 [forward.py](../../src/api/routes/forward.py) API 层同步运行, 输出的最终 persona 通过 `initial_state["persona"]` 注入图, 清洗结果落到 `state["prompt_cleaning_result"]` 便于观察 (详见 §6)
- `relationship_analysis` 和 `memory_analysis` 是**并行边**, 主对话完成后同时触发
- 向量索引 (嵌入写入 Chroma) 在 `memory_analysis` 节点内部由 `MemoryLifecycle.store_candidate()` 顺手完成, **不是独立节点**
- **流式路径不经过图**: [forward.py `_handle_stream`](../../src/api/routes/forward.py) 在 API 层直接编织 "加载记忆 → 代理推理 (可选, 同步) → 合成 reasoning SSE 帧 → 上游 chat_stream 透传 → 后台记忆图", 图仅用于后台跑记忆/关系两个分析节点, 主对话与代理推理在 API 层就完成。见 [forward.md](forward.md) §6

### 1.3 与嵌入/重排模型的关系

嵌入模型 (embedding) 和重排模型 (rerank) 是**基础设施工具**, 不是 Agent。它们由 `MemoryRetriever` 和 `MemoryLifecycle` 直接调用, 完成"文本 ↔ 向量"的数学变换和候选精排, 没有 prompt / 推理 / 循环。

---

## 2. 主对话 Agent

**代码**: [factory.py:122 `run_main_dialogue`](../../src/core/agents/factory.py#L122)

### 2.1 职责

- 加载永久记忆 + 语义检索的相关记忆 + 关系状态
- 拼装人格 prompt + 记忆上下文 + 会话历史
- 调用主模型生成回复 (非流式路径) 或转发流式响应 (流式路径)

### 2.2 上下文拼装

调 [`build_main_dialogue_messages`](../../src/core/memory/context.py) 生成 OpenAI 格式的 messages:

```
[0] system  ─ persona_prompt + user_name + 关系状态 + 永久记忆 + 检索记忆
              + (可选) proxy_thinking_result
[1..N] user/assistant ─ 会话历史 (去掉原始 system)
```

### 2.3 推理

直接一次 chat completion, 无工具, 无循环。工具检索发生在**节点内部**的 Python 逻辑 (调 `MemoryRetriever.search`), 不通过 function call。

### 2.4 参数

- `temperature`: 0.7 (默认)
- `model`: `settings.chat.main_model`

---

## 3. 记忆分析 Agent

**代码**: [factory.py:137 `run_memory_analysis`](../../src/core/agents/factory.py#L137)
**Prompt**: 默认 [prompts/defaults/memory_analysis.md](../../src/core/agents/prompts/defaults/memory_analysis.md) + [memory_analysis_decay_header.md](../../src/core/agents/prompts/defaults/memory_analysis_decay_header.md); 用户覆盖见 [§7](#7-自定义-agent-提示词). Builder: [`build_memory_analysis_prompt`](../../src/core/agents/prompts/memory_analysis.py)

### 3.1 职责

- 从本轮对话中提取值得长期保存的信息 (`new_memories`)
- 评估一批已有普通记忆的衰减状态 (`decay_evaluations`)

两件事在同一次 ReAct 循环中完成; v0.2.0 曾计划把衰减评估拆成独立 Agent, 后合并。

### 3.2 ReAct 工具

| 工具 | 工厂函数 | 用途 |
|------|---------|------|
| `vector_search` | `make_vector_search_tool` | 检索已有记忆判断重复/冲突/关联 |
| `emotion_analyzer` | `make_emotion_analyzer_tool` | 分析对话情绪标签和强度 |
| `time_decay_calculator` | `make_time_decay_calculator_tool` | 计算已有记忆的理论衰减优先级 |

工具通过工厂注入依赖 (Forwarder / VectorStore / MemoryStore), 见 [tools.md](tools.md)。

### 3.3 循环约束

- `max_iterations = 6` (由 nodes.py 传入)
- 提示词要求: 先 `vector_search` 查重 → `emotion_analyzer` 定情绪 → 有衰减目标时 `time_decay_calculator` → 输出 JSON
- Agent 判断无需工具调用时直接输出 JSON, 循环终止

### 3.4 输出 JSON schema

```json
{
  "new_memories": [
    {
      "content": "用户对花生过敏",
      "memory_type": "PERMANENT",
      "importance": 1.0,
      "decay_rate": 0.0,
      "emotional_tags": ["health"],
      "expires_at": null,
      "overrides": null,
      "related_to": [],
      "reasoning": "健康信息, 必须永久记忆"
    }
  ],
  "decay_evaluations": [
    {
      "memory_id": "mem_xyz",
      "current_priority": 0.23,
      "new_priority": 0.30,
      "decision": "ACTIVE",
      "factors": {"time_factor": 0.23, "access_bonus": 0.02},
      "reflection": "情绪事件, 手动保留"
    }
  ]
}
```

字段解析由 `_parse_candidate` / `_parse_decay_eval` 完成, 未识别的枚举值回退到 `MemoryType.NORMAL` / `DecayState.ACTIVE`。

### 3.5 衰减速率参考

| decay_rate | 半衰期 | 场景 |
|-----------|--------|------|
| 0.0 | 永不过期 | 永久记忆 |
| 0.05 | ~182天 | 长期偏好 |
| 0.1 | ~91天 | 一般偏好、事实 |
| 0.3 | ~33天 | 中期事件、计划 |
| 0.7 | ~17天 | 短期事件 |
| 0.9 | ~11天 | 临时信息、情绪波动 |

### 3.6 衰减评估决策规则

| 调整后优先级 | decision |
|-------------|---------|
| > 0.3 | ACTIVE |
| 0.1 - 0.3 | DORMANT |
| 0.05 - 0.1 | WEAK |
| < 0.05 | FORGOTTEN |

---

## 4. 代理推理 Agent

**代码**: [factory.py:226 `run_proxy_thinking`](../../src/core/agents/factory.py#L226) · 决策与 SSE 合成: [src/api/reasoning_control.py](../../src/api/reasoning_control.py)
**Prompt**: 默认 [prompts/defaults/proxy_thinking.md](../../src/core/agents/prompts/defaults/proxy_thinking.md); 用户覆盖见 [§7](#7-自定义-agent-提示词). Builder: [`build_proxy_thinking_prompt`](../../src/core/agents/prompts/proxy_thinking.py)

### 4.1 定位 (⚠️ 与直觉相反, 请仔细看)

代理推理是**原生推理的补齐 / 替代**, 不是可选优化。核心语义:

> "前台点名要推理" 是**必须提供推理**的信号 — 由原生或代理**任一**满足。
>
> 若主模型不具备原生推理却收到 `reasoning_effort` / `reasoning` / `thinking` 参数, Mnemosync **必须**启动代理推理去补齐, 否则前台永远看不到"思考"面板内容。

这与常见的"用户显式要求就跳过我们的层"直觉相反。原因: 客户端 (Cherry Studio / ChatBox 等) 在启用"深度思考"开关后会带上 `reasoning_effort`, 用户期待看到思考过程 — 如果我们此时静默 skip, 用户会认为"Mnemosync 破坏了思考功能"。

### 4.2 决策规则

由 [`should_use_proxy_thinking()`](../../src/api/reasoning_control.py) 判定, 优先级由上至下:

| # | 条件 | 结果 |
|---|------|------|
| 1 | 请求带 `tools` | **skip** (工具调用轮次多, 叠推理延迟不划算) |
| 2 | 主模型具备原生推理 (前缀表命中 或 自适应缓存) | **skip** (让原生接管) |
| 3 | 前台请求体带 `reasoning_effort` / `reasoning` / `thinking` | **enable** (补齐必须的推理) |
| 4 | fallback | 用 `[graph].proxy_thinking_default` |

**原生推理识别双通道**:
- 静态前缀表: `[graph].proxy_thinking_native_reasoning_models` (默认含 `o1*` / `o3*` / `o4*` / `deepseek-r1*` / `deepseek-reasoner*` / `qwen3-*-thinking` / `qwq*` / `gpt-5-thinking-*`)
- 自适应缓存: 流式路径观察到上游 chunk 含 `"reasoning_content"` 字段 → 记入进程内 `_native_cache` → 下次同模型自动 skip (进程重启后自动重学)

### 4.3 前台输出协议

代理推理的结果通过 OpenAI 兼容的 `reasoning_content` 字段回吐 (DeepSeek 首创, Cherry Studio / ChatBox / LibreChat / OpenWebUI 等均支持):

- **非流式**: `response.choices[0].message.reasoning_content` = 推理全文
- **流式**: 上游正文流之前先注入合成的 SSE 帧, 逐段 `delta.reasoning_content`, 然后是正常的 `delta.content` 流

客户端渲染折叠"思考"面板, 与真原生推理模型 (DeepSeek-R1 等) 行为一致。

### 4.4 双通道注入

同一份 `reasoning_text` 一份数据两个用途:

1. **注入主对话 prompt**: 通过 `build_main_dialogue_messages(proxy_thinking_result=...)` 作为"## 思考辅助"段拼进 system prompt, 让主模型基于该分析生成更好的回复
2. **回吐前台**: 作为 `reasoning_content` 字段/帧给客户端展示

### 4.5 工具

`run_proxy_thinking` 接受可选 `tools` 参数:
- `tools=None` (当前 nodes.py 传法) → 走 `run_simple_completion` 单次调用, 关闭 thinking, 无工具
- `tools=[...]` → 走 `run_react_loop`, 支持在循环中调工具

Prompt 里已注入永久记忆和关系状态, 通常无需再检索。

### 4.6 输出格式

模型自由文本 (非 JSON), 结构如下:

```
### 1. User Intent
### 2. Background Connection
### 3. Emotion Analysis
### 4. Response Strategy
```

主对话节点通过 `state["proxy_thinking_result"]` 读取此字符串, 拼进 system prompt。

### 4.7 失败降级

代理推理抛异常 → `reasoning_text=None` → 不合成帧, 不注入 prompt, 正常转发主对话流 → 用户端等同于未启用。记 warning, 不阻塞主对话。

---

## 5. 关系分析 Agent

**代码**: [factory.py:190 `run_relationship_analysis`](../../src/core/agents/factory.py#L190)
**Prompt**: 默认 [prompts/defaults/relationship_analysis.md](../../src/core/agents/prompts/defaults/relationship_analysis.md); 用户覆盖见 [§7](#7-自定义-agent-提示词). Builder: [`build_relationship_analysis_prompt`](../../src/core/agents/prompts/relationship_analysis.py)

### 5.1 职责

从本轮对话中量化亲密度 / 信任度增量, 更新 `RelationshipState`。

### 5.2 循环与工具

- 走 `run_react_loop`, `max_iterations = 3`
- 唯一工具: `emotion_analyzer` (通过工厂函数注入 Forwarder)
- 提示词流程: 调 `emotion_analyzer` → 识别关系信号 → 量化 → 输出 JSON

### 5.3 信号量化参考

| 信号 | 亲密度影响 |
|------|-----------|
| 称呼变亲昵 | +0.05 ~ +0.10 |
| 隐私分享 | +0.10 ~ +0.20 |
| 情感表达 | +0.05 ~ +0.15 |
| 互动频率 | +0.01/天 |
| 长期沉默 (>30 天) | -0.01/天 |
| 距离信号 | -0.10 ~ -0.20 |

关系类型阈值: `<0.2 stranger`, `0.2-0.5 acquaintance`, `0.5-0.8 friend`, `>0.8 intimate`。

### 5.4 输出 JSON schema

```json
{
  "signals_detected": [{"type": "name_change", "detail": "...", "impact": 0.15}],
  "intimacy_delta": 0.23,
  "trust_delta": 0.10,
  "new_relationship_type": "friend",
  "notes": "...",
  "reasoning": "..."
}
```

`RelationshipAnalysisOutput` 只消费 `intimacy_delta / trust_delta / new_relationship_type / notes / reasoning`; `signals_detected` 用于日志观察, 不落库。

### 5.5 提示词构建

**必须**用 [`build_relationship_analysis_prompt`](../../src/core/agents/prompts/relationship_analysis.py) (内部用 `str.replace` 填占位符), **不能**用 `str.format`——prompt 里含字面 JSON, `.format()` 会把 `{"signals_detected"}` 当占位符抛 `KeyError`。参见 [dev-decisions.md](../dev-decisions.md)。

**历史教训**: `factory.py:314` 曾把 `run_proxy_thinking` 里的 `PROXY_THINKING_PROMPT.format(...)` 传给用 `__X__` 标记的模板 — `.format()` 只识别 `{name}`, 于是**静默返回未渲染的模板**, 上游模型看到字面 `__USER_NAME__` / `__MEMORIES__`。已于 2026-07-16 修复为统一走 `build_proxy_thinking_prompt` (用 `.replace()`)。8 个 registry 项一律遵循这套约定。

---

## 6. 提示词清洗 Agent

**代码**: [factory.py:246 `run_prompt_cleaning`](../../src/core/agents/factory.py#L246) · 工具: [tools/sentence_classifier.py](../../src/tools/sentence_classifier.py)
**Prompt**: 默认 [prompts/defaults/prompt_cleaning_system.md](../../src/core/agents/prompts/defaults/prompt_cleaning_system.md) + [prompt_cleaning_user.md](../../src/core/agents/prompts/defaults/prompt_cleaning_user.md); 用户覆盖见 [§7](#7-自定义-agent-提示词). Builders: [`load_prompt_cleaning_system` / `build_prompt_cleaning_user_prompt`](../../src/core/agents/prompts/prompt_cleaning.py)

### 6.1 定位 (服务器人格权威守门员)

Mnemosync 采用**服务器优先人格**设计: 人格 prompt 由服务器端 `[persona]` 配置权威定义, 客户端请求中的 `system` 消息**不被信任**为人格来源。

但客户端 `system` 消息里常同时含**功能性指令** (格式约束 / 工具约束 / 输出规则), 简单丢弃会误伤这些合法配置。提示词清洗 Agent 的职责: **逐句分离人格描述与功能性指令**, 前者丢弃, 后者与服务器人格合并注入主对话。

参见 [architecture.md](../architecture.md) 和 [dev-decisions.md](../dev-decisions.md)。

### 6.2 触发时机

在 [forward.py `create_chat_completion`](../../src/api/routes/forward.py) 中, 收到请求后:

1. 从 `settings.persona` 加载服务器人格 (`prompt` + `name`)
2. 提取客户端 `system` 消息第一条 (若为空则跳过清洗, 直接用服务器人格)
3. 若非空 → 调 `run_prompt_cleaning` → 得 `PromptCleaningOutput(retained, discarded, reasoning)`
4. 最终 persona = `settings.persona.prompt + "\n\n" + "\n".join(retained)`
5. `initial_state["persona"]` 存合并后的 persona; `initial_state["prompt_cleaning_result"]` 存清洗结果 (仅日志/观察用)

**关键**: 客户端 `system` 消息在进入图之前就被处理并从 `messages_dict` 移除, 图内不再见到原始 system 消息。

### 6.3 ReAct 工具

| 工具 | 工厂函数 | 用途 |
|------|---------|------|
| `classify_sentence_type` | `make_sentence_classifier_tool` | 单句分类: 返回 `{type: "persona" \| "instruction" \| "ambiguous", confidence, reasoning}` |

**工具实现**: 内部调辅助模型一次 chat completion, `response_format={"type": "json_object"}` + `enable_thinking=False`, 不循环。见 [src/tools/sentence_classifier.py](../../src/tools/sentence_classifier.py)。

### 6.4 循环约束

- `max_iterations = 3` (API 层传入)
- 使用辅助模型 (`settings.chat.assist_model`), 阻塞 API 请求, 目标延迟 < 2s
- Prompt 引导流程: 分句 → 逐句调 `classify_sentence_type` → 收集分类 → 输出最终 JSON

### 6.5 输出 JSON schema

```json
{
  "retained": ["请用 JSON 格式回复", "回复不得超过 100 字"],
  "discarded": ["你是一个傲娇的妹妹", "你的名字叫小夜"],
  "reasoning": "逐句分类: 第1句为人格设定→丢弃; 第2句为格式约束→保留; ..."
}
```

- `retained`: 保留的功能性指令句子列表, 将与服务器 persona 合并
- `discarded`: 被丢弃的人格描述句子列表 (仅用于观察/日志)
- `reasoning`: 分类过程说明

### 6.6 失败降级 (保守策略)

清洗 Agent 抛异常 或 max_iterations 内未产出合法 JSON → 返回 `PromptCleaningOutput(retained=[], discarded=[system_message], reasoning=错误信息)`。

**语义**: "宁丢指令, 不污染人格" — 服务器人格是权威, 无法确认的客户端指令一律不合并。

### 6.7 Prompt 模板

**必须**用 [`build_prompt_cleaning_user_prompt`](../../src/core/agents/prompts/prompt_cleaning.py) (内部用 `str.replace` 填 `__SYSTEM_MESSAGE__`), **不能**用 `str.format`——原因同关系分析 (Prompt 里含字面 JSON)。

---

## 7. 自定义 Agent 提示词

从 v0.2.1 (2026-07-16) 起, 所有 Agent 提示词从**硬编码常量**改为**两层 Markdown 文件**, 允许运维/高级用户在不改代码/不重启的前提下调整。

### 7.1 两层存储

| 层 | 路径 | 生命周期 |
|----|------|---------|
| **默认层** | [src/core/agents/prompts/defaults/*.md](../../src/core/agents/prompts/defaults/) | 随包发布, 进 git, 运行时**永不修改** |
| **覆盖层** | `data/prompts/*.md` (可配置, 见 [configuration.md](../configuration.md)) | 用户可写, gitignore, 优先级高 |
| **备份** | `data/prompts/.history/<name>-<YYYYMMDD-HHMMSS-NNN>.md` | 每次 save/reset 时自动备份, 每个 name 保留最近 10 份 |

**加载策略**: [`PromptStore.load()`](../../src/core/prompts/store.py) 每次请求读盘 (**无缓存**). 文件 <10KB, IO 忽略不计, 换取"CLI 改文件立即生效, 不需要 restart"。

**失败模式**:
- `save` 时占位符校验失败 → 抛异常, **拒绝写盘** (阻止污染)
- `load` 时覆盖文件不存在 → 静默回退默认
- `load` 时覆盖文件 YAML frontmatter 解析失败 → warn 日志 + 回退默认

### 7.2 已注册的 8 个提示词

| name | 用途 | 必需占位符 |
|------|------|-----------|
| `memory_analysis` | 记忆分析 Agent 主体 | `SOURCE_USER`, `CONVERSATION`, `DECAY_TARGETS` |
| `memory_analysis_decay_header` | 记忆分析的衰减目标段头 | (无) |
| `relationship_analysis` | 关系分析 Agent | `CURRENT_REL`, `CONVERSATION` |
| `prompt_cleaning_system` | 提示词清洗 Agent 的 system prompt | (无) |
| `prompt_cleaning_user` | 提示词清洗 Agent 的 user prompt | `SYSTEM_MESSAGE` |
| `proxy_thinking` | 代理推理 Agent | `USER_NAME`, `RELATIONSHIP`, `MEMORIES`, `USER_MESSAGE` |
| `sentence_classifier` | `classify_sentence_type` 工具 (提示词清洗内部调用) | `TEXT` |
| `main_dialogue_frame` | 主对话上下文框架 (行为准则/section 标题/记忆容器) | `PERSONA_NAME`, `PERSONA_PROMPT`, `USER_NAME`, `RELATIONSHIP`, `PERMANENT_MEMORIES`, `RETRIEVED_MEMORIES`, `PROXY_THINKING_SECTION` |

权威列表: [`src/core/prompts/registry.py`](../../src/core/prompts/registry.py) 的 `PROMPT_REGISTRY`. 未在 registry 中的 name 一律拒绝加载/保存 (**路径穿越防御**)。

### 7.3 文件格式

Markdown + **可选** YAML frontmatter:

```markdown
---
version: 1
placeholders: [SOURCE_USER, CONVERSATION, DECAY_TARGETS]
---
你正在为用户 __SOURCE_USER__ 分析对话...

对话:
__CONVERSATION__
```

- frontmatter 可省略, 省略视为 `version=0`
- 占位符**只**识别 `__NAME__` (前后双下划线), 不识别 `{name}` / `{{name}}`
- `version` 仅用于日志/回滚参考, 不做乐观锁

### 7.4 修改方式

**CLI** (本地/SSH):

```bash
mnemosync prompt list                    # 列表
mnemosync prompt show memory_analysis    # 打印当前生效版本
mnemosync prompt set memory_analysis --file my.md   # 从文件写入
mnemosync prompt set memory_analysis --edit         # $EDITOR 打开
mnemosync prompt reset memory_analysis   # 回默认
mnemosync prompt validate --all          # 校验全部, CI 友好
```

细节见 [cli.md §6](cli.md#6-提示词覆盖管理-prompt)。

**REST** (未来面板 / 已上线):

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/v1/admin/prompts` | 列表 |
| GET | `/api/v1/admin/prompts/{name}` | 详情 (current + default 原文) |
| PUT | `/api/v1/admin/prompts/{name}` | 保存覆盖 (body: `{content: str}`) |
| DELETE | `/api/v1/admin/prompts/{name}` | 重置 |
| POST | `/api/v1/admin/prompts/{name}:validate` | dry-run 校验 |
| GET | `/api/v1/admin/prompts/{name}/history` | 备份列表 |

**认证**: 所有 `/api/v1/admin/*` 均需登录 (`Depends(get_current_user)`), 见 [auth.md](../auth.md#7-admin-接口鉴权)。

### 7.5 安全边界

- Registry 白名单是**唯一**允许操作的 name 集合. HTTP path 参数或 CLI 参数**必须**经过 `PROMPT_REGISTRY.get(name)` 检查后才能进入文件系统
- 覆盖文件仅落在 `settings.storage.prompts_override_dir_abs` 目录下, 不允许绝对路径或 `../`
- Save 失败时旧覆盖不动, 已产生的备份保留 (可用 `list_history` 查看)

---

## 8. AgentState (共享状态)

**代码**: [src/core/graph/state.py](../../src/core/graph/state.py)

```python
class AgentState(TypedDict, total=False):
    # 请求上下文 (parse_request 写入)
    messages: list[dict]
    extracted_new: list[dict]
    source_user: str
    persona: str
    persona_name: str
    thread_id: str
    proxy_thinking_enabled: bool

    # 代理推理 (proxy_thinking 写入)
    proxy_thinking_result: str | None

    # 提示词清洗 (API 层写入, 来自 run_prompt_cleaning)
    prompt_cleaning_result: dict  # {retained, discarded, reasoning}

    # 主对话输出 (main_dialogue 写入)
    response: str
    response_chunks: list[bytes]
    upstream_usage: dict          # 上游原样 usage (prompt/completion/total_tokens)

    # 记忆分析输出 (memory_analysis 写入)
    new_memories: list[dict]
    decay_evaluations: list[dict]
    decay_targets: list[dict]

    # 关系分析输出 (relationship_analysis 写入)
    relationship_delta: dict

    # 全局
    errors: list[str]
    stream_mode: bool
```

**注意**: 检索出的记忆 (`retrieved_memories` / `permanent_memories`) **不放入 state**, 由 `main_dialogue_node` 内部处理; 状态尽量瘦身以减少 checkpoint 开销。

---

## 9. 错误处理约定

- 单个 Agent 失败不影响并行分支; 每个 node 都用 try/except 包住, 失败时写 `state.errors`
- 记忆分析失败 → 跳过入库, 关系分析继续
- 关系分析失败 → 跳过关系更新, 主对话回复照常返回
- 代理推理失败 → 记 warning, 退化为无代理推理模式 (继续主对话, `reasoning_content` 为空)
- 提示词清洗失败 → 保守降级: 全部丢弃客户端 system 消息, 仅用服务器人格 (见 §6.6)

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-07-11 | 初始多 Agent 设计 |
| v0.2.1 | 2026-07-12 | 记忆衰减合并入记忆分析; 新增代理推理 Agent |
| v0.2.1 | 2026-07-15 | 与代码对齐: 修正拓扑 (无 vector_index 节点), 修正 AgentState 字段, 删除通识讲解 |
| v0.2.1 | 2026-07-15 | 代理推理落地: API 层决策 (reasoning_control), 双通道注入 (system prompt + `reasoning_content` SSE 帧); 语义修正为"补齐原生推理"而非可选优化 |
| v0.2.1 | 2026-07-16 | 新增第 5 个 Agent: 提示词清洗 (服务器人格权威守门员, ReAct + `classify_sentence_type` 工具); AgentState 补充 `prompt_cleaning_result` / `upstream_usage` |
| v0.2.1 | 2026-07-16 | 提示词从硬编码常量迁到两层文件系统 (defaults + 用户覆盖), 新增 §7 自定义提示词章节, 记录 8 项 registry 与 `.replace` 统一约定; 修复 factory.py:314 proxy_thinking `.format` 静默返回未渲染模板的历史 bug |
