<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  addModelBinding,
  deleteModelBinding,
  listUpstreamAvailableModels,
  probeEmbeddingDimension,
  startMemoryReindex,
  updateModelBinding,
} from '@/api/client'
import type {
  RoleBindingItem,
  RoleBindingUpdateBody,
  UpstreamModelType,
  UpstreamService,
} from '@/types/api'

const ROLE_TITLES: Record<UpstreamModelType, string> = {
  main: '主模型',
  assist: '辅助模型',
  embedding: '嵌入模型',
  rerank: '重排序模型',
}

type Mode = 'add' | 'replace' | 'edit'

const props = defineProps<{
  services: UpstreamService[]
  bindings: Record<UpstreamModelType, RoleBindingItem[]>
}>()

const emit = defineEmits<{
  saved: []
}>()

const visible = ref(false)
const formRef = ref<FormInstance | null>(null)
const form = reactive({
  role: 'main' as UpstreamModelType,
  service_id: '',
  model: '',
  priority: null as number | null,
  context_length: 128000 as number | null,
  embedding_dim: null as number | null,
  send_dimensions: false,
})
const submitting = ref(false)
const dimProbing = ref(false)
const availableModels = ref<string[]>([])
const availableLoading = ref(false)

const mode = ref<Mode>('add')
const editingTarget = ref<RoleBindingItem | null>(null)

const rules: FormRules = {
  service_id: [{ required: true, message: '请选择服务商', trigger: 'change' }],
  model: [{ required: true, message: '请填写模型名', trigger: 'blur' }],
}

const priorityCap = computed(() => props.bindings[form.role]?.length ?? 0)
const isEmbeddingForm = computed(() => form.role === 'embedding')
const showPriorityField = computed(() => mode.value === 'add' && !isEmbeddingForm.value)

const dialogTitle = computed(() => {
  const roleName = ROLE_TITLES[form.role]
  if (mode.value === 'replace') return `替换: ${roleName}`
  if (mode.value === 'edit' && editingTarget.value) {
    return `编辑: ${roleName} · #${editingTarget.value.priority}`
  }
  return `添加候选: ${roleName}`
})

const submitLabel = computed(() => {
  if (mode.value === 'replace') return '替换'
  if (mode.value === 'edit') return '保存'
  return '添加'
})

const DEFAULT_CONTEXT_LENGTH: Record<UpstreamModelType, number> = {
  main: 128000,
  assist: 128000,
  rerank: 128000,
  embedding: 8192,
}

function resetForm(role: UpstreamModelType) {
  form.role = role
  form.service_id = props.services[0]?.id ?? ''
  form.model = ''
  form.priority = null
  form.context_length = DEFAULT_CONTEXT_LENGTH[role]
  form.embedding_dim = null
  form.send_dimensions = false
  availableModels.value = []
}

function openAdd(role: UpstreamModelType) {
  mode.value = 'add'
  editingTarget.value = null
  resetForm(role)
  visible.value = true
  if (form.service_id) fetchAvailable(form.service_id)
}

function openReplace(existing: RoleBindingItem) {
  mode.value = 'replace'
  editingTarget.value = existing
  resetForm(existing.role)
  visible.value = true
  if (form.service_id) fetchAvailable(form.service_id)
}

function openEdit(existing: RoleBindingItem) {
  mode.value = 'edit'
  editingTarget.value = existing
  form.role = existing.role
  form.service_id = existing.service_id
  form.model = existing.model
  form.priority = existing.priority
  form.context_length = existing.context_length
  form.embedding_dim = existing.embedding_dim
  form.send_dimensions = existing.send_dimensions
  availableModels.value = []
  visible.value = true
  if (form.service_id) fetchAvailable(form.service_id)
}

defineExpose({ openAdd, openReplace, openEdit })

async function fetchAvailable(id: string) {
  availableLoading.value = true
  try {
    const res = await listUpstreamAvailableModels(id)
    availableModels.value = res.models
  } catch (err) {
    availableModels.value = []
    ElMessage.warning(
      '拉取模型列表失败, 可手动输入: ' +
        (err instanceof Error ? err.message : String(err)),
    )
  } finally {
    availableLoading.value = false
  }
}

function onServiceChange(id: string) {
  if (mode.value !== 'edit') {
    form.model = ''
  }
  availableModels.value = []
  if (id) fetchAvailable(id)
}

async function onProbeDim() {
  if (!form.service_id || !form.model.trim()) {
    ElMessage.warning('请先选择服务与模型')
    return
  }
  dimProbing.value = true
  try {
    const res = await probeEmbeddingDimension({
      service_id: form.service_id,
      model: form.model.trim(),
      dimensions: form.embedding_dim,
    })
    form.embedding_dim = res.dimensions
    ElMessage.success(`探测成功: ${res.dimensions} 维`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    dimProbing.value = false
  }
}

function diffForEdit(target: RoleBindingItem): RoleBindingUpdateBody {
  const patch: RoleBindingUpdateBody = {}
  const trimmedService = form.service_id.trim()
  const trimmedModel = form.model.trim()
  if (trimmedService !== target.service_id) patch.service_id = trimmedService
  if (trimmedModel !== target.model) patch.model = trimmedModel
  if (form.context_length !== target.context_length) {
    patch.context_length = form.context_length
  }
  if (isEmbeddingForm.value) {
    if (form.embedding_dim !== target.embedding_dim) {
      patch.embedding_dim = form.embedding_dim
    }
    if (form.send_dimensions !== target.send_dimensions) {
      patch.send_dimensions = form.send_dimensions
    }
  }
  return patch
}

async function submitAddOrReplace() {
  if (mode.value === 'replace' && editingTarget.value) {
    await deleteModelBinding(editingTarget.value.role, editingTarget.value.priority)
  }
  await addModelBinding({
    role: form.role,
    service_id: form.service_id,
    model: form.model.trim(),
    priority: form.priority,
    context_length: form.context_length,
    embedding_dim: form.embedding_dim,
    send_dimensions: form.send_dimensions,
  })
  ElMessage.success(mode.value === 'replace' ? '已替换' : '已添加')
}

async function submitEdit() {
  if (!editingTarget.value) return
  const patch = diffForEdit(editingTarget.value)
  if (Object.keys(patch).length === 0) {
    ElMessage.info('没有变更')
    visible.value = false
    return
  }
  await updateModelBinding(editingTarget.value.role, editingTarget.value.priority, patch)
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
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="100px"
    >
      <el-form-item label="服务商" prop="service_id">
        <el-select
          v-model="form.service_id"
          placeholder="选择上游服务"
          style="width: 100%"
          @change="onServiceChange"
        >
          <el-option
            v-for="s in services"
            :key="s.id"
            :label="s.id"
            :value="s.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="模型名" prop="model">
        <el-select
          v-model="form.model"
          filterable
          allow-create
          default-first-option
          placeholder="从可用列表选择或直接输入"
          style="width: 100%"
          :loading="availableLoading"
        >
          <el-option
            v-for="m in availableModels"
            :key="m"
            :label="m"
            :value="m"
          />
        </el-select>
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
      <el-form-item v-if="mode === 'edit'" label="优先级">
        <div class="readonly-priority">
          <span class="mono">#{{ form.priority }}</span>
          <span class="hint hint-inline">编辑不改优先级; 请用列表上下箭头调整</span>
        </div>
      </el-form-item>
      <el-form-item label="上下文长度">
        <el-input-number
          v-model="form.context_length"
          :min="1"
          placeholder="用于预估截取上下文 (token)"
          style="width: 100%"
          controls-position="right"
        />
      </el-form-item>
      <el-form-item v-if="isEmbeddingForm" label="嵌入维度">
        <div class="dim-row">
          <el-input-number
            v-model="form.embedding_dim"
            :min="1"
            placeholder="可变维模型请手填或点探测"
            style="flex: 1"
            controls-position="right"
          />
          <el-button
            :loading="dimProbing"
            :disabled="!form.service_id || !form.model"
            @click="onProbeDim"
          >
            探测维度
          </el-button>
        </div>
        <div class="hint">
          用作向量库维度锁 (VectorStore 会据此校验后续写入). 点「探测」会向上游发送一次 "hi" 请求读取真实输出维度。
        </div>
      </el-form-item>
      <el-form-item v-if="isEmbeddingForm" label="透传 dimensions">
        <el-checkbox v-model="form.send_dimensions" :disabled="!form.embedding_dim">
          把维度作为 <code>dimensions</code> 参数发给上游
        </el-checkbox>
        <div class="hint">
          <strong>默认不开</strong>. 仅可变维模型需要开启:
          <code>text-embedding-3-*</code> / <code>text-embedding-v3/v4</code> /
          <code>qwen3-embedding-*</code>.
          固定维模型 (<code>bge-*</code> / <code>bce-*</code> / <code>jina-*</code> /
          Mistral / Gemini) 开启会被上游拒绝 (400 "parameter is invalid").
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
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: $space-1;
  line-height: 1.4;
}

.hint-inline {
  margin-top: 0;
  margin-left: $space-2;
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
</style>
