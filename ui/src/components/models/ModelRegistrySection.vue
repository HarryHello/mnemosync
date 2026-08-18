<!-- 模型注册表区 (v0.4.1, RFC model-registry).

两种模式:
- 创建模式 (serviceId 为空): 本地暂存待添加模型 (model/display_name/concurrency),
  父组件创建服务后取 modelValue 提交 (importRegistryModels / createRegistryModel)。
- 编辑模式 (serviceId 非空): 实时 CRUD 该服务商的注册表模型 + 「从上游导入」。
-->
<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Plus } from '@element-plus/icons-vue'
import {
  createRegistryModel,
  deleteRegistryModel,
  importRegistryModels,
  listRegistryModels,
  listUpstreamAvailableModels,
  updateRegistryModel,
} from '@/api/client'
import type { ModelRegistryItem } from '@/types/api'

export interface PendingModel {
  model: string
  display_name?: string
  concurrency?: number
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

// ── 创建模式: 暂存行 ──────────────────────────────────────────────────────
const pending = ref<PendingModel[]>([])

watch(
  () => props.modelValue,
  (v) => {
    pending.value = v ? v.map((m) => ({ ...m })) : []
  },
  { immediate: true, deep: true },
)

function addRow() {
  pending.value.push({ model: '', concurrency: 20 })
  flush()
}

function removeRow(i: number) {
  pending.value.splice(i, 1)
  flush()
}

function flush() {
  emit('update:modelValue', pending.value.map((m) => ({ ...m })))
}

// ── 编辑模式: 实时 CRUD ────────────────────────────────────────────────────
const newModel = ref({ model: '', display_name: '' })

async function reload() {
  if (!props.serviceId) return
  models.value = await listRegistryModels(props.serviceId)
}

async function addModel() {
  const name = newModel.value.model.trim()
  if (!name) {
    ElMessage.warning('请填写模型名')
    return
  }
  saving.value = true
  try {
    await createRegistryModel({
      service_id: props.serviceId!,
      model: name,
      display_name: newModel.value.display_name.trim() || null,
    })
    newModel.value = { model: '', display_name: '' }
    ElMessage.success('已注册')
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

async function removeModel(item: ModelRegistryItem) {
  try {
    await ElMessageBox.confirm(
      `删除模型 ` + '\'' + item.model + '\'' + `? 若已被角色绑定引用将被拒绝。`,
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
    const { models: names } = await listUpstreamAvailableModels(props.serviceId)
    if (!names.length) {
      ElMessage.info('上游未返回可用模型 (可手动添加)')
      return
    }
    const res = await importRegistryModels({ service_id: props.serviceId, models: names })
    ElMessage.success(`导入完成: 新增 ${res.added} 个, 跳过 ${res.skipped} 个`)
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
    if (id) reload()
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
        </template>
        <el-button size="small" type="primary" @click="isCreateMode() ? addRow() : addModel()">
          <el-icon><Plus /></el-icon>
          <span>添加模型</span>
        </el-button>
      </div>
    </div>

    <!-- 创建模式: 暂存行 -->
    <template v-if="isCreateMode()">
      <div class="tip">
        创建服务后, 以下模型将随服务一并注册。模型名必填, 显示名/并发数可选。
      </div>
      <div v-for="(row, i) in pending" :key="i" class="pending-row">
        <el-input
          v-model="row.model"
          placeholder="模型名 (如 deepseek-chat)"
          size="small"
        />
        <el-input
          v-model="row.display_name"
          placeholder="显示名 (可选)"
          size="small"
        />
        <el-input-number
          v-model="row.concurrency"
          :min="0"
          size="small"
          placeholder="并发"
        />
        <el-button size="small" :icon="Delete" @click="removeRow(i)" />
      </div>
      <div v-if="!pending.length" @click="addRow" class="empty-add">
        + 添加一个模型
      </div>
    </template>

    <!-- 编辑模式: 实时表格 -->
    <template v-else>
      <div class="tip">
        能力 (模态/上下文/维度) 与并发数归属模型; 绑定到角色时随注册表, 无需逐条填写。
      </div>
      <el-table :data="models" size="small" border>
        <el-table-column prop="model" label="模型名" min-width="150">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <span class="mono">{{ row.model }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="显示名" min-width="110">
          <template #default="{ row }: { row: ModelRegistryItem }">
            {{ row.display_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="能力" min-width="130">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <span class="caps">{{ row.input_modalities?.join('/') || 'text' }}</span>
            <span v-if="row.context_length" class="caps muted">
              · {{ Math.round(row.context_length / 1000) }}k
            </span>
            <span v-if="row.embedding_dim" class="caps muted">· {{ row.embedding_dim }}d</span>
          </template>
        </el-table-column>
        <el-table-column label="并发" width="130">
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
        <el-table-column label="启用" width="90">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <el-switch :model-value="row.enabled" @change="(v: string | number | boolean) => setEnabled(row, v === true)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }: { row: ModelRegistryItem }">
            <el-button link type="danger" size="small" @click="removeModel(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!models.length" description="该服务尚未注册模型" :image-size="60" />
      <div class="add-row">
        <el-input v-model="newModel.model" placeholder="模型名" size="small" style="flex:1" />
        <el-input
          v-model="newModel.display_name"
          placeholder="显示名 (可选)"
          size="small"
          style="flex:1"
        />
        <el-button size="small" type="primary" :loading="saving" @click="addModel">
          注册
        </el-button>
      </div>
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

  .pending-row {
    display: flex;
    gap: $space-2;
    align-items: center;
    margin-bottom: $space-2;

    :deep(.el-input-number) {
      width: 110px;
    }
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

  .add-row {
    display: flex;
    gap: $space-2;
    margin-top: $space-2;
  }
}
</style>
