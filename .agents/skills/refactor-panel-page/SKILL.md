---
name: refactor-panel-page
description: 重构或创建管理面板页面，重点是复用组件和合理拆分抽象
---

# 管理面板页面重构/创建指南

## 页面结构规范

所有管理面板页面应遵循以下统一结构：

```vue
<template>
  <div class="page-container">
    <PageHeader title="页面标题" subtitle="可选副标题">
      <template #actions>
        <!-- 操作按钮：刷新、新增等 -->
      </template>
    </PageHeader>

    <!-- 主内容区域：表格、卡片、标签页等 -->

    <!-- 弹窗组件：创建、编辑、确认等 -->
  </div>
</template>
```

### 多标签页页面

如果页面包含多个标签页，按以下方式组织：

```vue
<template>
  <div class="page-container">
    <!-- 页面级 PageHeader（如果有全局操作） -->
    <PageHeader v-if="hasGlobalActions" ... />

    <el-tabs v-model="activeTab">
      <el-tab-pane label="标签1" name="tab1">
        <Tab1Component />
      </el-tab-pane>
      <el-tab-pane label="标签2" name="tab2">
        <Tab2Component :active="activeTab === 'tab2'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
```

- 每个标签页的内容拆分为独立组件（如 `PromptListTab.vue`、`PersonaEditorTab.vue`）
- 通过 `:active` prop 通知子组件仅在激活时加载数据
- 标签页内部可继续按「页面结构规范」拆分（表格、弹窗等）

**参考**：`ui/src/views/PromptsPage.vue`

### 必须使用的通用组件

- **PageHeader** (`@/components/common/PageHeader.vue`)
  - Props: `title` (必填), `subtitle` (可选)
  - Slots: `actions` (右侧按钮区), `subtitle` (自定义副标题)
  - 替代手写的 `.page-head` 结构

## 组件拆分原则

### 何时拆分

当页面满足以下任一条件时应考虑拆分：
1. 单文件超过 200 行
2. 包含多个独立弹窗（创建、编辑、详情展示）
3. 包含可复用的样式模板
4. 包含可复用的或逻辑相对独立的表格/列表区块
5. 包含多个功能独立的标签页

### 拆分粒度

按功能职责拆分，而非按 UI 元素拆分：

```
ui/src/views/ExamplePage.vue (编排层)
  └─ 状态管理 + 事件处理
  └─ 调用子组件

ui/src/components/example/
  ├─ ExampleTable.vue (列表展示 + 行操作 emit)
  ├─ ExampleCreateDialog.vue (创建表单弹窗)
  ├─ ExampleEditDialog.vue (编辑表单弹窗)
  └─ ExampleDetailDialog.vue (详情展示弹窗)
```

### 子组件设计规范

#### 1. 表格组件 (`XxxTable.vue`)

```vue
<script setup lang="ts">
defineProps<{
  items: ItemType[]
  loading?: boolean
}>()

defineEmits<{
  edit: [row: ItemType]
  delete: [row: ItemType]
}>()
</script>
```

- 只负责展示，不调用 API
- 通过 emit 向上传递用户操作
- 内部可包含：行格式化、掩码显示、日期格式化等纯展示逻辑

#### 2. 弹窗组件 (`XxxCreateDialog.vue` / `XxxEditDialog.vue`)

```vue
<script setup lang="ts">
const props = defineProps<{
  modelValue: boolean
  submitting?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [payload: PayloadType]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
</script>
```

- 使用 `v-model` (`modelValue` + `update:modelValue`) 控制显示
- `submitting` prop 防止重复提交
- 表单验证在内部完成，通过 `submit` emit 传出有效数据
- 重新打开时应清空内部状态（watch `modelValue` 从 false 变 true）

#### 3. 详情展示弹窗 (`XxxDetailDialog.vue`)

```vue
<script setup lang="ts">
defineProps<{
  modelValue: boolean
  item: ItemType | null
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
  copy: [text: string]
  closed: []
}>()
</script>
```

- 接收完整数据项进行展示
- 可包含复制等快捷操作
- `closed` 事件用于清理父组件状态

## 可复用样式与工具

### SCSS 变量（自动注入，无需显式 import）

```scss
// 间距
$space-1 (4px), $space-2 (8px), $space-3 (12px), $space-4 (16px), $space-5 (24px)

// 圆角
$radius-sm (4px), $radius-md (8px), $radius-lg (12px)

// 工具类
.mono           // 等宽字体（用于 ID、Key、URL 等）
.muted          // 次要文字颜色
.page-container // 页面容器（已在 global.scss 定义）
.page-title     // 页面标题（已在 global.scss 定义）
.page-subtitle  // 页面副标题（已在 global.scss 定义）
```

### Mixins

```scss
flex-center()   // flex 居中
card-shadow()   // 卡片阴影
truncate        // 单行截断
line-clamp(3)   // 多行截断
respond-to(md)  // 断点响应式
```

## API 调用规范

### 响应处理

- 204 No Content 响应在 `client.ts` 中已统一处理为返回 `undefined`
- 非 JSON 响应会自动抛出错误
- 使用 `ElMessage` 展示成功/错误提示
- 危险操作使用 `ElMessageBox.confirm` 确认

### 错误处理模式

```typescript
async function doSomething() {
  loading.value = true
  try {
    await apiCall()
    ElMessage.success('操作成功')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}
```

## 重构 Checklist

### 1. 整体结构
- [ ] 替换手写 page-head 为 PageHeader 组件
- [ ] 确认使用 `.page-container` 包裹
- [ ] 移除不再需要的自定义样式（page-head, head-actions 等）

### 2. 组件拆分
- [ ] 识别可独立的表格区块 → `XxxTable.vue`
- [ ] 识别可独立的创建弹窗 → `XxxCreateDialog.vue`
- [ ] 识别可独立的编辑弹窗 → `XxxEditDialog.vue`
- [ ] 识别可独立的详情展示 → `XxxDetailDialog.vue`
- [ ] 在 `ui/src/components/` 下创建对应功能目录

### 3. 数据流梳理
- [ ] 页面组件只保留：状态、refresh、事件处理函数
- [ ] 表格组件只通过 props 接收数据，通过 emit 传递操作
- [ ] 弹窗组件只通过 v-model 控制显隐，通过 emit 传递提交

### 4. 样式清理
- [ ] 移除内联样式
- [ ] 改用 SCSS 变量
- [ ] 复用已有的工具类（.mono, .muted 等）

## 参考案例

### ApiKeysPage 重构

**重构前**：单文件 300+ 行，包含：
- 手写 page-head
- 内联表格
- 内联创建弹窗
- 内联密钥展示弹窗
- 所有样式混在一起

**重构后**：
- `ApiKeysPage.vue` (~100 行) - 编排层
- `ApiKeyTable.vue` - 表格展示 + copy/revoke emit
- `ApiKeyCreateDialog.vue` - 创建表单弹窗
- `ApiKeySecretDialog.vue` - 密钥展示弹窗

### 参考文件
- `ui/src/views/ApiKeysPage.vue` - 已重构的页面
- `ui/src/views/PromptsPage.vue` - 标签页拆分示例
- `ui/src/views/ModelsPage.vue` - 复杂分组组件示例
