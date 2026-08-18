<!-- 角色绑定对话框 (v0.4.1: 从模型注册表选模型).

能力字段 (模态/上下文/嵌入维) 随注册表, 此处只读展示, 不再逐条填写.
支持: 添加 / 替换 (嵌入单绑定, 带 Reindex 引导) / 换模型 (PATCH model_id).
-->
<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  addModelBinding,
  deleteModelBinding,
  probeEmbeddingDimension,
  startMemoryReindex,
  updateModelBinding,
  updateRegistryModel,
} from '@/api/client'
import type {
  ModelRegistryItem,
  RoleBindingItem,
  UpstreamModelType,
} from '@/types/api'

const ROLE_TITLES: Record<UpstreamModelType, string> = {
  main: '主模型',
  assist: '辅助模型',
  embedding: '嵌入模型',
  rerank: '重排序模型',
}

type Mode = 'add' | 'replace' | 'edit'

const props = defineProps<{
  models: ModelRegistryItem[]
  bindings: Record<UpstreamModelType, RoleBindingItem[]>
}>()

const emit = defineEmits<{
  saved: []
}>()

const visible = ref(false)
const formRef = ref<FormInstance | null>(null)
const form = reactive({
  role: 'main' as UpstreamModelType,
  model_id: '',
  priority: null as number | null,
})

const submitting = ref(false)
const dimProbing = ref(false)
const mode = ref<Mode>('add')
const editingTarget = ref<RoleBindingItem | null>(null)

const rules: FormRules = {
  model_id: [{ required: true, message: '请选择模型', trigger: 'change' }],
}

const priorityCap = computed(() => props.bindings[form.role]?.length ?? 0)
const isEmbeddingForm = computed(() => form.role === 'embedding')
const showPriorityField = computed(() => mode.value === 'add' && !isEmbeddingForm.value)

// 当前选中模型 (只读能力展示)
const selectedModel = computed<ModelRegistryItem | null>(() => {
  return props.models.find((m) => m.id === form.model_id) ?? null
})

const dialogTitle = computed(() => {
  const roleName = ROLE_TITLES[form.role]
  if (mode.value === 'replace') return `替换: ${roleName}`
  if (mode.value === 'edit' && editingTarget.value) {
    return `换模型: ${roleName} · #${editingTarget.value.priority}`
  }
  return `添加候选: ${roleName}`
})

const submitLabel = computed(() => {
  if (mode.value === 'replace') return '替换'
  if (mode.value === 'edit') return '保存'
  return '添加'
})

function resetForm(role: UpstreamModelType) {
  form.role = role
  form.model_id = ''
  form.priority = null
}

function openAdd(role: UpstreamModelType) {
  mode.value = 'add'
  editingTarget.value = null
  resetForm(role)
  visible.value = true
}

function openReplace(existing: RoleBindingItem) {
  mode.value = 'replace'
  editingTarget.value = existing
  resetForm(existing.role)
  form.model_id = existing.model_id || `${existing.service_id}:${existing.model}`
  visible.value = true
}

function openEdit(existing: RoleBindingItem) {
  mode.value = 'edit'
  editingTarget.value = existing
  resetForm(existing.role)
  form.model_id = existing.model_id || `${existing.service_id}:${existing.model}`
  form.priority = existing.priority
  visible.value = true
}

defineExpose({ openAdd, openReplace, openEdit })

async function onProbeDim() {
  const m = selectedModel.value
  if (!m) return
  dimProbing.value = true
  try {
    const res = await probeEmbeddingDimension({
      service_id: m.service_id,
      model: m.model,
      dimensions: m.embedding_dim ?? undefined,
    })
    await updateRegistryModel(m.id, { embedding_dim: res.dimensions })
    ElMessage.success(`探测成功: ${res.dimensions} 维 (已写入注册表)`)
    emit('saved')  // 父组件 refresh 会重拉注册表
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    dimProbing.value = false
  }
}

function modelLabel(m: ModelRegistryItem): string {
  const name = m.display_name || m.model
  return `${name} (${m.service_id})`
}

async function submitAddOrReplace() {
  if (mode.value === 'replace' && editingTarget.value) {
    await deleteModelBinding(editingTarget.value.role, editingTarget.value.priority)
  }
  await addModelBinding({
    role: form.role,
    model_id: form.model_id,
    priority: form.priority,
  })
  ElMessage.success(mode.value === 'replace' ? '已替换' : '已添加')
}

async function submitEdit() {
  if (!editingTarget.value) return
  if (!form.model_id || form.model_id === editingTarget.value.model_id) {
    ElMessage.info('模型未变更')
    visible.value = false
    return
  }
  await updateModelBinding(editingTarget.value.role, editingTarget.value.priority, {
    model_id: form.model_id,
  })
  ElMessage.success('已保存')
}

async function onSubmit() {
  if (!formRef.value) return
  const ok = await formRef.value.validate().catch(() => false)
  if (!ok) return
  submitting.value = true
  try {
    if (mode.value === 'edit') {
      await submitEdit()
    } else {
      await submitAddOrReplace()
    }
    visible.value = false
    emit('saved')

    if (mode.value === 'replace') {
      try {
        await ElMessageBox.confirm(
          '嵌入模型已替换, 是否立即启动 Reindex? 重建期间新记忆会被拒绝入库。',
          '启动重建',
          {
            type: 'info',
            confirmButtonText: '现在重建',
            cancelButtonText: '稍后手动',
          },
        )
        await startMemoryReindex({ prune: false })
        ElMessage.success('Reindex 已启动, 请前往「记忆」页面查看进度')
      } catch {
        ElMessage.info('稍后可在「记忆 → 维护」中手动启动 Reindex')
      }
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="dialogTitle" width="560px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
      <el-form-item label="模型" prop="model_id">
        <el-select
          v-model="form.model_id"
          filterable
          placeholder="从模型注册表选择"
          style="width: 100%"
        >
          <el-option
            v-for="m in models"
            :key="m.id"
            :label="modelLabel(m)"
            :value="m.id"
          />
        </el-select>
        <div class="hint">
          从『上游 API → 模型配置 / 模型注册表』添加模型。能力字段 (模态/上下文) 随注册表。
        </div>
      </el-form-item>

      <el-form-item v-if="selectedModel" label="能力">
        <div class="caps">
          <el-tag size="small" type="info">
            {{ selectedModel.input_modalities?.join('/') || 'text' }}
          </el-tag>
          <span v-if="selectedModel.context_length" class="muted">
            · {{ Math.round(selectedModel.context_length / 1000) }}k 上下文
          </span>
          <span v-if="selectedModel.embedding_dim" class="muted">
            · {{ selectedModel.embedding_dim }} 维
          </span>
          <span class="muted">
            · 并发 {{ selectedModel.concurrency }}{{ selectedModel.concurrency === 0 ? ' (不限)' : '' }}
          </span>
          <el-tag v-if="!selectedModel.enabled" size="small" type="danger">已禁用</el-tag>
        </div>
      </el-form-item>

      <el-form-item v-if="showPriorityField" label="优先级">
        <el-input-number
          v-model="form.priority"
          :min="0"
          :max="priorityCap"
          :placeholder="`留空则排到末尾 (当前末尾: ${priorityCap})`"
          style="width: 100%"
          controls-position="right"
        />
        <div class="hint">
          0 为最高优先级; 留空则追加到末尾。指定已被占用的位置时, 现有候选会向后顺移。
        </div>
      </el-form-item>
      <el-form-item v-else-if="mode === 'edit'" label="优先级">
        <div class="readonly-priority">
          <span class="mono">#{{ form.priority }}</span>
          <span class="hint hint-inline">编辑不改优先级; 请用列表上下箭头调整</span>
        </div>
      </el-form-item>

      <el-form-item v-if="isEmbeddingForm && selectedModel" label="嵌入维度">
        <div class="dim-row">
          <span class="muted">
            {{ selectedModel.embedding_dim ?? '未设置' }} 维 (注册表)
          </span>
          <el-button :loading="dimProbing" @click="onProbeDim">
            探测并写入
          </el-button>
        </div>
        <div class="hint">
          探测结果会写入模型注册表; 嵌入模型换绑已存向量会失效, 需重跑 Reindex。
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">
        {{ submitLabel }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style lang="scss" scoped>
.hint {
  margin-top: $space-1;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}

.hint-inline {
  margin-top: 0;
  margin-left: $space-2;
}

.muted {
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: var(--el-font-family-mono, monospace);
}

.dim-row {
  display: flex;
  gap: $space-2;
  align-items: center;
  width: 100%;
}

.readonly-priority {
  display: flex;
  align-items: center;
  color: var(--el-text-color-regular);
}

.caps {
  display: flex;
  flex-wrap: wrap;
  gap: $space-2;
  align-items: center;
}
</style>
