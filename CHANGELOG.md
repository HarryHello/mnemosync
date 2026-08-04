# CHANGELOG

本文件记录 Mnemosync 的主要版本变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [v0.3.5] - 2026-07

### 安全
- 升级 starlette 修复已知安全漏洞，并启用 CI 阻断性检查。
- 修复对话历史中跨用户上下文混入的问题（`b1ac477`）。

### 架构
- 实现前端-后端分离（panel + backend 进程分离）。
- 修复 `docx/design/to-fix.md` 中 8 个问题。

### 工程
- 升级全部依赖至最新版本（v0.3.5）。
- 修复全部 mypy 错误（244 → 0），分解 `_handle_stream` 等相关重构。
- 将 `SqliteRelationshipStore` 拆分，修复若干快速问题。

## [v0.3.4] - 2026-06

### 安全
- 收紧 CORS 来源限制，移除未鉴权的 init 端点。
- 首次登录强制改账号密码（在 v0.3.4 前已引入，此处合入）。

### 功能
- 身份插件市场：远程安装 / 插件配置 / 管理页面。
- 实现 `agent-run-contract` RFC。
- 前端增加插件管理页、GitHub 链接、API Key 策略选择器。

### 修复
- 修复 memory graph 任务创建后立即被取消的问题。
- SSE 流式透传优化 + memory graph 错误处理 + `fmtDate` + http_log 连接修复。
- 规模化技术债清理（`refactor: complete remaining tech debt fixes`）。

### 工程
- 从 package metadata 读取版本号，不再硬编码。
- 补充 auth / forwarder / identity / memory lifecycle / plugin manager 单元测试。

## [v0.3.3] - 2026-05

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

## [v0.3.2] - 2026-05

### 工程
- 全量技术债修复（`refactor: 全量技术债修复 (v0.3.2)`）。

## [v0.3.1] - 2026-04

### 功能
- 工具能力：工具参数隐私确定性检查、API Key 级工具策略过滤、逻辑交互事务与幂等重放、工具冷却持久化与记忆治理端点、模型候选工具能力声明。
- 提示词：触发原因注入与工具隐私边界、平台能力提示与选择性参与指南、Expressor 表达改写层。
- 管理：记忆治理面板（批量删除 + 时间过滤）、工具策略管理 UI。
- 调试：管线语义事件（工具策略 / 事务 / 触发原因 / Expressor 改写对比）、交互事务聚合展示、评估维度统计端点。
- 跨平台身份绑定：指令触发 + 内部 tool 自然语言触发。
- 空间级串行锁：同一空间请求逐条处理。

### 工程
- 全局 API Key 级频率限制 + 冷却拦截调试事件。

## [v0.3.0] - 2026-03

### 功能
- 单人格 → 多人格、多用户体系演进（核心）。
- 身份 / 空间 / 受众（Audience）多用户基础：移除 `"default"` 硬编码。
- 新增身份模块，受众过滤据此决定记忆可见性。
- 空间记忆诞生标记（`space_id`）与受众过滤。

### 工程
- 版本号升至 v0.3.0，全量文档对齐多用户身份契约。
- 适配 v0.3.0 身份契约变更。

## [v0.2.x] - 2026-01 ~ 2026-02

### v0.2.13
- 通知中心：记忆入库失败即时上抛。

### v0.2.12
- 首次登录强制改账号密码（服务端硬拦 + `/setup` 页）。

### v0.2.11
- 发布准备与依赖同步。

### v0.2.10
- 关系称呼动态演化 + 人格面板编辑。

### v0.2.8
- 嵌入 `send_dimensions` 透传开关。
- 面板健康端点改读 package metadata。

### v0.2.7
- 人格状态重置端点。

### v0.2.6
- 跨前端短期记忆（双窗口装填）。

### v0.2.5
- 调试聊天面板 + HTTP hop 观测。

### v0.2.4
- 嵌入单绑定 + Reindex + Prune + 模型元数据。

### v0.2.1
- 提示词两层配置 + admin 鉴权 + sentence_classifier。

### v0.2.0
- 核心架构连通：依赖升级、核心数据模型、连通性验证。
- 文档与结构同步至 v0.2 布局。