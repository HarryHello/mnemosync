# RFC: 模型注册表 (Model Registry) — 把模型提升为一等实体

> **状态**: 已评审 (2026-08-17, 决策全部落定)
> **创建**: 2026-08-17
> **目标版本**: v0.4.1 兼容增强 (版本号不变, 升级兼容)
> **作者**: HarryHelloo (AI 协作)
> **关联**: 早前 AstrBot 「配置上游 → 添加模型 → 配置模型选择」一体化心智讨论

---

## 1. 背景与动机

当前模型管理的三层结构让「模型」无法作为独立实体被管理:

| 表 | 内容 | 问题 |
|----|------|------|
| `llm_services` | id / base_url / api_key / api_format | 正常 |
| `model_configs` | service_id / model / model_type | **半成品注册表** — 无能力/显示名/并发, 主链路不消费 |
| `role_bindings` | role / priority / service_id / model / **context_length / embedding_dim / modalities** | **能力字段每条绑定重复声明一份**; 绑定时先选服务商再手输/搜模型名 |

具体痛点:

1. **绑定时能力重复配置**: 同一个模型被 main/assist 引用两次, 能力字段 (input/output_modalities 等) 要填两遍; 模型能力升级 (比如服务商开放 image) 要逐条改。
2. **模型没有归属点**: 并发上限无处置放; 「模型显示名」无处可存 (多模态下拉、面板展示都只能显示裸模型名)。
3. **Onboarding 割裂**: 新建服务商与配置模型是两个 tab, 上游表单里没有添加模型的入口, 必须创建后再切 tab。
4. **/v1/models 逐次探测**: 模型能力判断 (如 Vision 是否透传图片) 依赖请求时解析上游, 而非查表。

## 2. 目标 / 非目标

### 目标

- 新增 **models 注册表**: service_id (FK) + 模型名 + 显示名 + 能力 (模态/上下文/嵌入维) + 并发数。
- 上游服务商表单内嵌「添加/导入模型」入口 (轻量版)。
- 角色绑定时**从注册表选模型** (展示 服务商/显示名/能力), 不再先选服务商、不再手输模型名。
- 能力字段收拢到模型表, 绑定只引用 `model_id`。

### 非目标 (本版本不做)

- **执行层 per-model 并发限流**: concurrency 本版本只入库 + UI 管理; MultiForwarder 的 per-model 信号量/队列放 v0.5。
- 模型级定价/成本统计、按模型的密钥轮换。

## 3. 数据模型

```sql
CREATE TABLE models (
    id                TEXT PRIMARY KEY,      -- 复合 id: {service_id}:{model}
    service_id        TEXT NOT NULL REFERENCES llm_services(id) ON DELETE CASCADE,
    model             TEXT NOT NULL,         -- 上游模型名 (deepseek-chat)
    display_name      TEXT,                  -- 展示名 (空则回落 model)
    input_modalities  TEXT NOT NULL DEFAULT '["text"]',   -- 从 role_bindings 收拢
    output_modalities TEXT NOT NULL DEFAULT '["text"]',
    context_length    INTEGER,               -- 从 role_bindings 收拢
    embedding_dim     INTEGER,               -- 从 role_bindings 收拢 (嵌入模型属性)
    send_dimensions   INTEGER NOT NULL DEFAULT 0,
    concurrency       INTEGER,               -- 并发上限; NULL = 不限 (仅管理, 执行层 v0.5)
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMP NOT NULL,
    updated_at        TIMESTAMP NOT NULL,
    UNIQUE (service_id, model)
);
CREATE INDEX idx_models_service ON models(service_id);
```

`role_bindings` 演进:

```sql
-- 新增列 (迁移 009):
ALTER TABLE role_bindings ADD COLUMN model_id TEXT REFERENCES models(id);
-- 能力列 (context_length/embedding_dim/send_dimensions/input_modalities/output_modalities)
-- 迁移后不再写入, 按项目惯例保留至 v0.6 移除
```

`model_configs` 并入 `models` 后进入废弃通道 (同 favor 旧列处理, v0.6 删表)。

## 4. 关键设计决策

| # | 决策点 | 推荐 | 理由 |
|---|--------|------|------|
| D1 | 模型 id 策略 | **复合主键 `{service_id}:{model}`** (评审定) | service_id 创建后不可改 (编辑表单只有 base_url/key/格式), 模型名在上游唯一, 复合 id 稳定且可读; 绑定引用即服务商+模型名 |
| D2 | 能力字段归属 | 全部收拢到 `models` | 能力是**模型属性**不是绑定属性; 同一模型跨角色能力必然一致 |
| D3 | 并发数语义 | 本版本**只存不用**; **默认 20, 0 = 不限** (评审定) | 执行层 (per-model 信号量) 放 v0.5; UI 数字输入 + 0 = 不限 + 默认 20 |
| D4 | 绑定 API | body 直接改 `model_id` 语义, **不保留旧 body** (评审定) | 同仓库前后端同步发布, 无外部消费者; store 层内部按 model_id 解析 |
| D5 | 删除被引用模型 | **拒绝** (提示先解绑) | 绑定级联删除会造成静默失配; 明确失败优于隐式 |
| D6 | /v1/models | **保持现状: 恒返回 `mnemosync-any` 虚拟模型** (评审定) | 客户端永远只看到 mnemosync-any, 具体模型由 mnemosync 内部管理; 前台能力静态声明: 文本输入 + 图片输入 + 思考 + 文本输出 + 工具调用 |

## 5. 迁移方案 (兼容 v0.4.1, 幂等 + 可回滚)

走现有 `MigrationRunner` (失败回滚 + 抛异常):

1. **007**: 建 `models` 表 (IF NOT EXISTS)。
2. **008**: 从 `role_bindings` 去重合并回填 —
   ```sql
   INSERT OR IGNORE INTO models (id, service_id, model, input_modalities, ...)
   SELECT lower(hex(randomblob(16))), service_id, model, MAX(input_modalities), ...
   FROM role_bindings GROUP BY service_id, model;
   ```
   (同 service+model 多绑定取第一个非空能力值; 全部字段走 COALESCE 防 NULL 破坏 NOT NULL/DEFAULT)。
3. **009**: `role_bindings` 加 `model_id` 列并回填:
   ```sql
   UPDATE role_bindings SET model_id = (
       SELECT id FROM models
       WHERE models.service_id = role_bindings.service_id
         AND models.model = role_bindings.model
   );
   ```
4. **010**: `model_configs` → `models` 合并 (按 service+model, INSERT OR IGNORE, model_type 映射为 "kind" 注记或丢弃), 之后 `model_configs` 只读不写。

新库 CREATE TABLE 直接含新结构 (无迁移)。旧列不删, 保持「只能加列」的升级规矩。

## 6. API 设计

### 模型注册表 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/models?service_id=` | 列表 (可选按服务商过滤) |
| POST | `/admin/models` | 注册单个模型 |
| PATCH | `/admin/models/{model_id}` | 改显示名/能力/并发/enabled |
| DELETE | `/admin/models/{model_id}` | 删除 (被绑定引用时 409) |
| POST | `/admin/models:import` | 从 `/v1/models` 批量导入 (`{service_id, models: []}`, UNIQUE 冲突跳过, 返回 新增/跳过 数量) |

### 绑定改造

- `POST /admin/model-bindings`: body 支持 `{role, model_id, priority?}` (新) 与旧 `{role, service_id, model, ...能力}` (deprecated)。
- `RoleBindingItem` 响应字段追加 `model_id` / `display_name` / 服务商与能力 (经 join, 面板展示用)。

### /v1/models

返回注册表 enabled 模型聚合 (model 名 + 显示名 + 能力); 注册表为空 → 原有逐服务商上游探测逻辑回退。

## 7. 前端改动

| 文件 | 改动 |
|------|------|
| `UpstreamTab.vue` | 新增/编辑表单下方「模型」区: 该服务商注册表列表 (模型名/显示名/能力/并发/操作) + 「添加模型」行 + 「从上游导入」按钮 |
| 新建 `ModelRegistrySection.vue` | 上述区块 (供新增/编辑对话框复用) |
| `ModelBindingDialog.vue` | 模型选择数据源改为注册表列表 (展示 服务商/显示名/能力, 选中后只读呈现能力), 移除「先选服务商→搜模型」 |
| `ModelTab.vue` + `ModelBindingsSection.vue` | 绑定列表展示经 join 的显示名/服务商; 删除/重排逻辑不变 |
| `api/upstream.ts` + types | 新增 models CRUD / import / 绑定新 body 类型 |

## 8. 影响面与测试

- **后端**: llm_service store (+1 表 +3 迁移)、admin_upstream / 新 admin_models 路由、schemas、/v1/models 聚合、RoleResolver 绑定解析 (改经 model 表 join)。
- **测试**: 迁移回填 (模拟带 5 迁移列的旧库 → 升新结构, 验证绑定/能力/服务商不丢)、模型 CRUD + import、绑定新旧 body 双兼容、/v1/models 聚合与空表回退。
- **前端**: vitest 补 ModelRegistrySection 渲染 + 绑定选择交互。
- **风险**: 迁移回填是唯一硬点 (能力字段 COALESCE/DEFAULT 必须完备); 全量回归需覆盖 961 现有测试。

## 9. 开放问题 (评审待定)

1. D1 的 uuid vs 复合可读 id — 是否有运维上需要可读 id 的场景?
2. `enabled` 列本版本是否引入 (还是只增删)?
3. 旧 `addModelBinding` body 的 deprecated 保留期 (v0.6 移除?);
4. /v1/models 空表回退是否值得做 (还是直接返回空列表 + 面板引导);
5. concurrency 的 UI 形态 (数字输入, 0 = 不限, 提示语要不要)。

## 10. 节奏建议

1. 评审本 RFC (拍板 §9 开放问题)
2. 后端: 迁移 + store + API + /v1/models (含测试)
3. 前端: 注册表区 + 绑定改造
4. 全量回归 (ruff/mypy/pytest/UI 三闸) → 提交
