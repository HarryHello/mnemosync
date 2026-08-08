# CHANGELOG

本文件记录 Mnemosync 的主要版本变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [v0.4.0] - 2026-08

### 功能
- **多模态视觉支持**：
  - 模型绑定新增 `input_modalities` / `output_modalities` 字段（默认 `["text"]`）。
  - 目标模型支持图片时，直接透传 image content parts。
  - 目标模型不支持图片时，新增 Vision Description Agent（ASSIST 角色）自动将图片转述为文字描述，流式与非流式路径均支持。
- **Anthropic API 双向兼容**：
  - 上游：服务商新增 `api_format` 字段，`anthropic` 格式经 `AnthropicForwarder`（anthropic SDK）转发，自动完成 OpenAI ↔ Messages 格式转换。
  - 下游：新增 `POST /v1/messages` 端点，接受 Anthropic Messages 格式请求。
- **OpenAI Responses API 双向兼容**：
  - 上游：`responses` 格式经 `ResponsesForwarder`（openai SDK）转发。
  - 下游：新增 `POST /v1/responses` 端点。
- **身份绑定增强**：
  - 绑定指令改为经 LLM 自然语气回复（`BindContext`），兼容流式/非流式，不再是硬编码文本。
  - 修复自绑定检查缺失（同平台绑定自己现在会被拒绝）。
  - 内部工具名加 `mnemosync_` 前缀，避免与客户端工具冲突。

### 架构
- 上游转发改用官方 SDK（`openai>=2.50.0`、`anthropic>=0.110.0`），替代 raw httpx；rerank 保留 httpx。
- 提取共享 `debug_utils.emit_upstream_debug`，消除三处 `_emit_debug` 复制粘贴。
- `MultiForwarder` 按 `api_format` 路由到 OpenAI / Anthropic / Responses 转发器；embed/rerank 仅支持 openai 格式并显式报错。

### 修复
- `_handle_bind_response` 中 `space_locks` 变量名错误导致运行时 `NameError`。
- Anthropic/Responses 转发器 `chatcmpl_id` 用 `int(time.time())` 导致同秒请求 ID 碰撞，改用 uuid。
- 移除重复的 `_emit_debug` 调用与未使用的 import。
- install.sh 依赖安装自动检测 PyPI 连通性，不通时切换清华/阿里镜像。

## [v0.3.5] - 2026-08

### 安全
- 修复群聊上下文混杂漏洞：`build_short_term_history` 增加 `source_user` 过滤，防止不同用户的对话历史泄露。
- 升级 starlette 1.0→1.3，修复 6 个 CVE。
- 群聊记忆用实际用户名（`CURRENT_SPEAKER`）作主语，替代模糊的"你"。

### 功能
- **前后端分离**：新增 `mnemosync panel`（轻量面板 16125）+ `mnemosync backend`（后端 16126），支持面板内启停后端。
- **Agent 运行契约**：`AgentSpec` 注册表、`AgentRunStore` 持久化、`run_agent_tracked()` 超时/追踪。
- **版本更新检测**：启动时检查 GitHub releases，有新版本自动通知；设置页面手动检查 + 升级按钮。
- **一次性升级通知**：v0.3.5 升级提醒用户重建向量库（chromadb 大版本升级）。
- **仪表盘改进**：健康卡片显示后端状态 + 启停按钮；上游配置测试按钮；自动刷新。
- **设置页面**：版本更新检测 + 升级按钮；重启服务按钮。
- **人格导入导出**：角色卡导入（已有）+ 导出按钮。
- CLI 新增 `mnemosync restart`、`mnemosync backend-stop`；daemon 模式显示端口。

### 架构
- 拆分 `nodes.py`（803 行→7 个模块包）。
- 拆分 `memory_store.py`（950 行→`memory_store` + `relationship_store`）。
- 拆分 `admin_persona.py`（837 行→8 个文件包）。
- 拆分 `admin_identity.py`（524 行→5 个文件包）。
- 提取 forward `_accessors.py` 解决循环依赖。
- `HttpLogStore` 改继承 `SqliteStore`。
- 所有 store 统一使用 `MigrationRunner` 管理 schema。
- langgraph 1.x config 注入 bug 修复（`from __future__ import annotations` 导致节点收不到 config）。

### 修复
- SSE 中间件消费 body 导致调试面板永远"连接中"。
- memory_graph 任务创建后立即被 finally 取消。
- `get_project_root()` 在安装环境下路径错误。
- `SystemHealthCard` 启停后健康数据不更新。
- 面板代理 `/panel/api-keys` 返回 404（代理范围不足）。
- `http_log_store` 的 `is_closed()` 无效调用。
- 9 个 Vue 组件 `fmtDate` 未定义。
- 日志页面分页无响应。

### 依赖升级
- langgraph 0.6→1.2、langchain-core 0.3→1.5（移除未使用的 langchain/langchain-openai）
- chromadb 0.6→1.5、cryptography 42→50
- fastapi 0.115→0.141、uvicorn 0.32→0.52、pydantic 2.9→2.13
- bcrypt 4.2→5.0、structlog 24.4→26.1、httpx 加 socksio 代理支持
- pytest 8.4→9.1、pytest-asyncio 0.26→1.4、ruff 0.12→0.16、mypy 1.17→2.3

### 工程
- mypy 580→0 错误，CI 改为阻断。
- 前端 ESLint 18 错误清零，CI 改为阻断。
- 补充 17 个新测试（crypto、relationship_store、admin_core、vector_search、forward integration、panel_routes、update_checker、identity_binding）。
- 收窄 `except Exception` 裸捕获，补充 `logger.debug`。
- 抽取 magic numbers 为命名常量。
- 面板代理改用请求头白名单。
- 添加 CHANGELOG.md。

---

## [v0.3.4] - 2026-08

### 安全
- 收紧 CORS 来源限制，移除未鉴权的 init 端点。
- 首次登录强制改账号密码（服务端硬拦 + `/setup` 页）。

### 功能
- 身份插件市场：远程安装 / 插件配置 / 管理页面。
- 实现 `agent-run-contract` RFC。
- 前端增加插件管理页、GitHub 链接、API Key 策略选择器。

### 修复
- 修复 memory graph 任务创建后立即被取消的问题。
- SSE 流式透传优化 + memory graph 错误处理 + `fmtDate` + http_log 连接修复。
- 规模化技术债清理。

### 工程
- 从 package metadata 读取版本号，不再硬编码。
- 补充 auth / forwarder / identity / memory lifecycle / plugin manager 单元测试。

---

## [v0.3.3] - 2026-07

### 功能
- 人格系统重构：支持多人格配置与切换（核心）。
- 结构化人格定义 + SQLite 存储 + 按空间覆盖。
- 空间社交策略：per-space Expressor 配置（§6 SocialPolicy）。
- 预定义知识存储（Lorebook）+ 关键词触发 + 管理 API。
- 角色卡导入：SillyTavern V1/V2/JSON 解析 + 导入 API。
- 人格面板结构化编辑 + 版本管理。
- 记忆纠正：`supersedes` 软替代 + 管理 API。

### 工程
- 加固安全审计并固定依赖到兼容版本。
- 修复 CI 若干问题（ruff lint、uv lockfile、npm audit、pip-audit）。
- 新增卸载脚本，并在 install.sh 中提示。

---

## [v0.2.11] - 2026-07

### 功能
- 工具能力：工具参数隐私确定性检查、API Key 级工具策略过滤、逻辑交互事务与幂等重放。
- 提示词：触发原因注入与工具隐私边界、Expressor 表达改写层。
- 管理：记忆治理面板（批量删除 + 时间过滤）、工具策略管理 UI。
- 调试：管线语义事件、交互事务聚合展示、评估维度统计端点。
- 跨平台身份绑定：指令触发 + 内部 tool 自然语言触发。
- 空间级串行锁：同一空间请求逐条处理。

### 工程
- 全局 API Key 级频率限制 + 冷却拦截调试事件。
