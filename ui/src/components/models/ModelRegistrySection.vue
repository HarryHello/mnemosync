<!-- 模型注册表区 (v0.4.1, RFC model-registry).

两种模式:
- 创建模式 (serviceId 为空): 本地暂存待添加模型 (两行: 模型选择+显示名+删除 /
  输入上限+输出上限+并发+能力多选), 父组件创建服务后取 modelValue 提交。
- 编辑模式 (serviceId 非空): 实时 CRUD + 从上游 /models 拉取 selector + 批量导入。
显示名默认 = {serviceId}/{model} (可改); 输入/输出上限支持 K/M 单位。
-->
<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close, Download, Plus } from '@element-plus/icons-vue'
import {
  createRegistryModel,
  deleteRegistryModel,
  importRegistryModels,
  listRegistryModels,
  listUpstreamAvailableModels,
  updateRegistryModel,
} from '@/api/client'
import type { ModelRegistryItem, UpstreamModelDetail } from '@/types/api'

export interface PendingModel {
  model: string
  display_name?: string
  concurrency?: number
  input_limit?: string           // 输入上限 (K/M 文本, 提交时解析)
  output_limit?: string          // 输出上限 (K/M 文本)
  capabilities?: string[]        // 能力: 模态 text/image/audio + tools(工具调用)
}

const props = defineProps<{
  serviceId?: string
  modelValue?: PendingModel[]
}>()

const emit = defineEmits<{
  'update:modelValue': [models: PendingModel[]]
}>()

const saving = ref(false)
const importing = ref(false)
const models = ref<ModelRegistryItem[]>([])

// 并发默认 20 (0 = 不限)
const DEFAULT_CONCURRENCY = 20

// ── K / M 单位解析 ─────────────────────────────────────────────────────────
function parseTokenLimit(v: string | undefined | null): number | null {
  const sv = (v || '').trim()
  if (!sv) return null
  const s = sv.toUpperCase()
  const m = /^(\d+(?:\.\d+)?)\s*([KM]?)$/.exec(s)
  if (!m) return null
  const n = parseFloat(m[1] ?? '')
  if (Number.isNaN(n)) return null
  if (m[2] === 'K') return Math.max(1, Math.round(n * 1024))
  if (m[2] === 'M') return Math.max(1, Math.round(n * 1024 * 1024))
  return Math.max(1, Math.round(n))
}

function formatTokenLimit(n: number | null | undefined): string {
  if (!n) return ''
  if (n >= 1024 * 1024 && n % (1024 * 1024) === 0) return String(n / (1024 * 1024)) + 'M'
  if (n >= 1024 && n % 1024 === 0) return String(n / 1024) + 'K'
  return String(n)
}

// ── 创建模式: 暂存行 (两行) ─────────────────────────────────────────────────
const pending = ref<PendingModel[]>([])

watch(
  () => props.modelValue,
  (v) => {
    pending.value = v
      ? v.map((m) => ({ ...m, capabilities: m.capabilities ? [...m.capabilities] : [] }))
      : []
  },
  { immediate: true, deep: true },
)

function addRow() {
  pending.value.push({
    model: '',
    concurrency: DEFAULT_CONCURRENCY,
    capabilities: ['text'],
  })
  flush()
}

function removeRow(i: number) {
  pending.value.splice(i, 1)
  flush()
}

function rowModelChanged(i: number) {
  const row = pending.value[i]
  if (!row) return
  if (row.model && row.model.trim() && !row.display_name) {
    row.display_name = defaultDisplay(row.model.trim())
  }
  flush()
}

function flush() {
  emit('update:modelValue', pending.value.map((m) => ({ ...m })))
}

// ── 编辑模式: 实时 CRUD ────────────────────────────────────────────────────
const availableModels = ref<string[]>([])
// 拉取到的模型详情 (id -> 上游能力), 用于选中后自动回填
const upstreamDetails = ref<Record<string, UpstreamModelDetail>>({})
const upstreamLoading = ref(false)

const addForm = reactive({
  model: '',
  display_name: '',
  input_limit: '',
  output_limit: '',
  concurrency: DEFAULT_CONCURRENCY,
  capabilities: ['text'] as string[],
})

function defaultDisplay(model: string): string {
  if (props.serviceId) return props.serviceId + '/' + model
  return model
}

function clearAddForm() {
  addForm.model = ''
  addForm.display_name = ''
  addForm.input_limit = ''
  addForm.output_limit = ''
  addForm.concurrency = DEFAULT_CONCURRENCY
  addForm.capabilities = ['text']
}

async function reload() {
  if (!props.serviceId) return
  models.value = await listRegistryModels(props.serviceId)
}

async function fetchUpstream() {
  // 添加模型时读取服务商的 /models 端点获取模型
  if (!props.serviceId) return
  upstreamLoading.value = true
  try {
    const { models: list } = await listUpstreamAvailableModels(props.serviceId)
    availableModels.value = list.map((d) => d.id)
    const map: Record<string, UpstreamModelDetail> = {}
    for (const d of list) map[d.id] = d
    upstreamDetails.value = map
  } catch (err) {
    availableModels.value = []
    upstreamDetails.value = {}
    ElMessage.warning(
      '拉取上游 /models 失败, 可手动输入: ' +
        (err instanceof Error ? err.message : String(err)),
    )
  } finally {
    upstreamLoading.value = false
  }
}

function onModelPick() {
  const chosen = addForm.model.trim()
  if (chosen && !addForm.display_name) {
    addForm.display_name = defaultDisplay(chosen)
  }
  // 自动回填上游声明的能力 (如 DeepSeek 的 context_length / modalities)
  const d = upstreamDetails.value[chosen]
  if (d) {
    if (!addForm.input_limit) addForm.input_limit = formatTokenLimit(d.context_length)
    if (!addForm.output_limit) addForm.output_limit = formatTokenLimit(d.output_limit)
    const mods = d.input_modalities?.length ? d.input_modalities : ['text']
    if (
      !addForm.capabilities.length ||
      (addForm.capabilities.length === 1 && addForm.capabilities[0] === 'text')
    ) {
      addForm.capabilities = [...mods, ...(d.supports_tools ? ['tools'] : [])]
    }
  }
}

async function submitAdd() {
  const name = addForm.model.trim()
  if (!name) {
    ElMessage.warning('请填写/选择模型 id')
    return
  }
  const cl = parseTokenLimit(addForm.input_limit)
  const ol = parseTokenLimit(addForm.output_limit)
  if (addForm.input_limit.trim() && cl === null) {
    ElMessage.warning('输入上限格式无效 (如 128K / 8M / 131072)')
    return
  }
  if (addForm.output_limit.trim() && ol === null) {
    ElMessage.warning('输出上限格式无效 (如 8K / 0.5M)')
    return
  }
  const conv = Number(addForm.concurrency)
  if (Number.isNaN(conv) || conv < 0) {
    ElMessage.warning('并发数必须 >= 0 (0 = 不限)')
    return
  }
  saving.value = true
  try {
    await createRegistryModel({
      service_id: props.serviceId!,
      model: name,
      display_name: addForm.display_name.trim() || defaultDisplay(name),
      context_length: cl,
      output_limit: ol,
      concurrency: conv,
      // 能力合并: tools 归属 supports_tools, 其余为输入模态
      supports_tools: addForm.capabilities.includes('tools'),
      input_modalities: addForm.capabilities.filter((c) => c !== 'tools'),
    })
    ElMessage.success('已注册')
    clearAddForm()
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function setEnabled(item: ModelRegistryItem, enabled: boolean) {
  try {
    await updateRegistryModel(item.id, { enabled })
    item.enabled = enabled
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    await reload()
  }
}

async function setConcurrency(item: ModelRegistryItem, value: number) {
  try {
    await updateRegistryModel(item.id, { concurrency: value })
    item.concurrency = value
    ElMessage.success('已更新并发数')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    await reload()
  }
}

async function editLimits(item: ModelRegistryItem) {
  // 简单行内能力编辑: 输入/输出上限
  try {
    const { value: res } = await ElMessageBox.prompt(
      '输入上限与输出上限 (K/M, 逗号分隔; 空保留原值)\n当前: '
        + formatTokenLimit(item.context_length) + ' / ' + formatTokenLimit(item.output_limit),
      '编辑 \u201c' + (item.display_name || item.model) + '\u201d 上限',
      {
        inputValue: formatTokenLimit(item.context_length) + ',' + formatTokenLimit(item.output_limit),
        inputPlaceholder: '如 128K,8K',
      },
    )
    const [il, ol] = res.split(/[,，]/).map((s) => s.trim())
    await updateRegistryModel(item.id, {
      context_length: il ? parseTokenLimit(il) : null,
      output_limit: ol ? parseTokenLimit(ol) : null,
    })
    ElMessage.success('已更新')
    await reload()
  } catch {
    /* 取消 */
  }
}

async function removeModel(item: ModelRegistryItem) {
  try {
    await ElMessageBox.confirm(
      '删除模型 \u201c' + item.model + '\u201d? 若已被角色绑定引用将被拒绝。',
      '删除模型',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteRegistryModel(item.id)
    ElMessage.success('已删除')
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function importFromUpstream() {
  if (!props.serviceId) return
  importing.value = true
  try {
    const { models: details } = await listUpstreamAvailableModels(props.serviceId)
    if (!details.length) {
      ElMessage.info('上游未返回可用模型 (可手动添加)')
      return
    }
    // 读取上游声明的模型能力 (尽力解析, 缺失回落默认)
    const res = await importRegistryModels({
      service_id: props.serviceId,
      models: details.map((d) => ({
        model: d.id,
        context_length: d.context_length ?? undefined,
        output_limit: d.output_limit ?? undefined,
        input_modalities: d.input_modalities?.length ? d.input_modalities : undefined,
        output_modalities: d.output_modalities?.length ? d.output_modalities : undefined,
      })),
    })
    ElMessage.success(
      '导入完成: 新增 ' + res.added + ' 个, 跳过 ' + res.skipped + ' 个 (已带上游能力声明)',
    )
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    importing.value = false
  }
}

const isCreateMode = () => !props.serviceId

onMounted(() => {
  if (props.serviceId) reload()
})

watch(
  () => props.serviceId,
  (id) => {
    if (id) {
      reload()
    } else {
      availableModels.value = []
    }
  },
)
</script>

<template>
  <div class="registry">
    <div class="registry-head">
      <div class="registry-title">
        模型配置
        <el-tag size="small" type="info" class="count-tag">
          {{ isCreateMode() ? pending.length : models.length }}
        </el-tag>
      </div>
      <div class="registry-actions">
        <template v-if="!isCreateMode()">
          <el-button :loading="importing" size="small" @click="importFromUpstream">
            <el-icon><Download /></el-icon>
            <span>从上游导入</span>
          </el-button>
          <el-button size="small" :loading="upstreamLoading" @click="fetchUpstream">
            <el-icon><Refresh /></el-icon>
            <span>拉取模型</span>
          </el-button>
        </template>
        <el-button v-if="isCreateMode()" size="small" type="primary" @click="addRow">
          <el-icon><Plus /></el-icon>
          <span>添加模型</span>
        </el-button>
      </div>
    </div>

    <!-- 创建模式: 暂存行 (两行) -->
    <template v-if="isCreateMode()">
      <div class="tip">
        创建服务后随服务一并注册。第一行模型选择(可输入) + 显示名 + 删除;
        第二行输入/输出上限 (K/M) + 并发数 + 能力多选。
      </div>
      <div v-for="(row, i) in pending" :key="i" class="pending-block">
        <div class="row-line">
          <el-select
            v-model="row.model"
            filterable
            allow-create
            placeholder="模型 id (输入或从上游 /models 拉取后选择)"
            size="small"
            style="flex: 2"
            @change="rowModelChanged(i)"
          />
          <el-input
            v-model="row.display_name"
            placeholder="显示名 (默认 服务商/模型)"
            size="small"
            style="flex: 2"
          />
          <el-button size="small" :icon="Close" @click="removeRow(i)" />
        </div>
        <div class="row-line second-row">
          <el-input v-model="row.input_limit" placeholder="输入上限 128K" size="small" />
          <el-input v-model="row.output_limit" placeholder="输出上限 8K" size="small" />
          <el-input
            v-model="row.concurrency"
            placeholder="并发 0 不限"
            size="small"
            style="width: 110px"
          />
          <el-select
            v-model="row.capabilities"
            multiple
            placeholder="能力 (模态/工具调用)"
            size="small"
            style="flex: 2"
          >
            <el-option label="文本" value="text" />
            <el-option label="图片" value="image" />
            <el-option label="音频" value="audio" />
            <el-option label="工具调用" value="tools" />
          </el-select>
        </div>
      </div>
      <div v-if="!pending.length" class="empty-add" @click="addRow">
        + 添加一个模型
      </div>
    </template>

    <!-- 编辑模式: 实时表格 + 两行添加表单 -->
    <template v-else>
      <div class="tip">
        从上游 /models 拉取后选择/输入模型 id; 显示名默认 = 服务商/模型 (可改);
        输入/输出上限支持 K/M; 能力多选决定 input_modalities。
      </div>
      <!-- 两行添加表单 -->
      <div class="add-block">
        <div class="row-line">
          <el-select
            v-model="addForm.model"
            filterable
            allow-create
            :loading="upstreamLoading"
            placeholder="模型 id (拉取后选择, 或直接输入)"
            style="flex: 2"
            @focus="fetchUpstream"
            @change="onModelPick"
          >
            <el-option v-for="m in availableModels" :key="m" :value="m" :label="m" />
          </el-select>
          <el-input
            v-model="addForm.display_name"
            placeholder="显示名 (默认 服务商/模型)"
            style="flex: 2"
          />
          <el-tooltip content="清空表单" placement="top">
            <el-button :icon="Close" @click="clearAddForm" />
          </el-tooltip>
        </div>
        <div class="row-line second-row">
          <el-input v-model="addForm.input_limit" placeholder="输入上限 128K" />
          <el-input v-model="addForm.output_limit" placeholder="输出上限 8K" />
          <el-input
            v-model="addForm.concurrency"
            placeholder="并发 0 不限"
            style="width: 110px"
          />
          <el-select
            v-model="addForm.capabilities"
            multiple
            placeholder="能力 (模态/工具调用)"
            style="flex: 2"
          >
            <el-option label="文本" value="text" />
            <el-option label="图片" value="image" />
            <el-option label="音频" value="audio" />
            <el-option label="工具调用" value="tools" />
          </el-select>
          <el-button type="primary" :loading="saving" @click="submitAdd">注册</el-button>
        </div>
      </div>
      <el-table :data="models" size="small" border>
        <el-table-column prop="model" label="模型名" min-width="140">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <span class="mono">{{ row.model }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="显示名" min-width="150">
          <template #default="{ row }: { row: ModelRegistryItem }">
            {{ row.display_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="能力" min-width="180">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <span class="caps">{{ (row.input_modalities?.join('/') || 'text') + (row.supports_tools ? '/工具' : '') }}</span>
            <span v-if="row.context_length" class="caps muted">· 入 {{ formatTokenLimit(row.context_length) }}</span>
            <span v-if="row.output_limit" class="caps muted">· 出 {{ formatTokenLimit(row.output_limit) }}</span>
            <span v-if="row.embedding_dim" class="caps muted">· {{ row.embedding_dim }}d</span>
          </template>
        </el-table-column>
        <el-table-column label="并发" width="120">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <el-input-number
              :model-value="row.concurrency"
              :min="0"
              size="small"
              controls-position="right"
              @change="(v: number) => setConcurrency(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <el-switch
              :model-value="row.enabled"
              @change="(v: string | number | boolean) => setEnabled(row, v === true)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <el-button link type="primary" size="small" @click="editLimits(row)">上限</el-button>
            <el-button link type="danger" size="small" @click="removeModel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!models.length" description="该服务尚未注册模型" :image-size="60" />
    </template>
  </div>
</template>

<style lang="scss" scoped>
.registry {
  padding-top: $space-3;
  margin-top: $space-3;
  border-top: 1px solid var(--el-border-color-lighter);

  .registry-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: $space-2;

    .registry-title {
      font-size: 14px;
      font-weight: 600;

      .count-tag {
        margin-left: $space-1;
      }
    }

    .registry-actions {
      display: flex;
      gap: $space-2;
    }
  }

  .tip {
    margin-bottom: $space-2;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .row-line {
    display: flex;
    gap: $space-2;
    align-items: center;
  }

  .second-row {
    margin-top: $space-2;

    :deep(.el-input) {
      flex: 1;
    }
  }

  .pending-block {
    padding: $space-2;
    margin-bottom: $space-2;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 4px;
  }

  .add-block {
    padding: $space-3;
    margin-bottom: $space-3;
    border: 1px dashed var(--el-color-primary);
    border-radius: 4px;
  }

  .empty-add {
    padding: $space-3;
    font-size: 13px;
    color: var(--el-text-color-secondary);
    text-align: center;
    cursor: pointer;
    border: 1px dashed var(--el-border-color);
    border-radius: 4px;

    &:hover {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary);
    }
  }

  .caps {
    font-size: 12px;
  }
}
</style>
