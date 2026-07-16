<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  listUpstreamServices,
  createUpstreamService,
  updateUpstreamService,
  deleteUpstreamService,
  bindUpstreamModel,
  listUpstreamAvailableModels,
} from '@/api/client'
import type { UpstreamModelType, UpstreamService } from '@/types/api'

const services = ref<UpstreamService[]>([])
const loading = ref(false)

const ROLE_LABELS: Record<UpstreamModelType, string> = {
  main: '主模型',
  assist: '辅助模型',
  embedding: '嵌入模型',
  rerank: '重排序',
}
const ROLE_ORDER: UpstreamModelType[] = ['main', 'assist', 'embedding', 'rerank']

// ── Create dialog ──────────────────────────────────────────────────────────
const createDialog = ref(false)
const createRef = ref<FormInstance | null>(null)
const createForm = reactive({ id: '', base_url: '', api_key: '' })
const createSubmitting = ref(false)

const createRules: FormRules = {
  id: [{ required: true, message: '请填写服务 ID', trigger: 'blur' }],
  base_url: [
    { required: true, message: '请填写 Base URL', trigger: 'blur' },
    { pattern: /^https?:\/\//, message: 'URL 必须以 http:// 或 https:// 开头', trigger: 'blur' },
  ],
  api_key: [{ required: true, message: '请填写 API Key', trigger: 'blur' }],
}

// ── Edit dialog ────────────────────────────────────────────────────────────
const editDialog = ref(false)
const editRef = ref<FormInstance | null>(null)
const editing = ref<UpstreamService | null>(null)
const editForm = reactive({ base_url: '', api_key: '' })
const editSubmitting = ref(false)

// ── Bind dialog ────────────────────────────────────────────────────────────
const bindDialog = ref(false)
const bindTarget = ref<UpstreamService | null>(null)
const bindForm = reactive({ model_type: 'main' as UpstreamModelType, model: '' })
const availableModels = ref<string[]>([])
const availableLoading = ref(false)
const bindSubmitting = ref(false)

const bindCurrent = computed(() => {
  if (!bindTarget.value) return ''
  return bindTarget.value.models[bindForm.model_type] ?? ''
})

// ──────────────────────────────────────────────────────────────────────────
async function refresh() {
  loading.value = true
  try {
    services.value = await listUpstreamServices()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.id = ''
  createForm.base_url = ''
  createForm.api_key = ''
  createDialog.value = true
}

async function onCreate() {
  if (!createRef.value) return
  const ok = await createRef.value.validate().catch(() => false)
  if (!ok) return
  createSubmitting.value = true
  try {
    await createUpstreamService({
      id: createForm.id.trim(),
      base_url: createForm.base_url.trim(),
      api_key: createForm.api_key.trim(),
    })
    ElMessage.success('已创建')
    createDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    createSubmitting.value = false
  }
}

function openEdit(row: UpstreamService) {
  editing.value = row
  editForm.base_url = row.base_url
  editForm.api_key = ''
  editDialog.value = true
}

async function onEdit() {
  if (!editing.value) return
  const payload: { base_url?: string; api_key?: string } = {}
  const nextUrl = editForm.base_url.trim()
  if (nextUrl && nextUrl !== editing.value.base_url) payload.base_url = nextUrl
  if (editForm.api_key.trim()) payload.api_key = editForm.api_key.trim()
  if (!payload.base_url && !payload.api_key) {
    editDialog.value = false
    return
  }
  editSubmitting.value = true
  try {
    await updateUpstreamService(editing.value.id, payload)
    ElMessage.success('已更新')
    editDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    editSubmitting.value = false
  }
}

async function onDelete(row: UpstreamService) {
  try {
    await ElMessageBox.confirm(
      `将删除服务 "${row.id}" 及其全部模型绑定, 不可恢复。确认删除？`,
      '删除上游服务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteUpstreamService(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function openBind(row: UpstreamService, role: UpstreamModelType = 'main') {
  bindTarget.value = row
  bindForm.model_type = role
  bindForm.model = row.models[role] ?? ''
  availableModels.value = []
  bindDialog.value = true
  fetchAvailable(row.id)
}

async function fetchAvailable(id: string) {
  availableLoading.value = true
  try {
    const res = await listUpstreamAvailableModels(id)
    availableModels.value = res.models
  } catch (err) {
    ElMessage.warning(
      '拉取模型列表失败, 你仍可手动输入: ' +
        (err instanceof Error ? err.message : String(err)),
    )
  } finally {
    availableLoading.value = false
  }
}

async function onBind() {
  if (!bindTarget.value) return
  const model = bindForm.model.trim()
  if (!model) {
    ElMessage.warning('请填写模型名')
    return
  }
  bindSubmitting.value = true
  try {
    await bindUpstreamModel(bindTarget.value.id, {
      model_type: bindForm.model_type,
      model,
    })
    ElMessage.success('已绑定')
    bindDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    bindSubmitting.value = false
  }
}

function fmtDate(s: string): string {
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">上游 API</h2>
        <p class="page-subtitle">
          管理 Mnemosync 使用的 OpenAI 兼容上游服务商 (如 DashScope), 以及每个角色绑定的模型名。
          API Key 存储时使用 Fernet 对称加密。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新增服务</span>
        </el-button>
      </div>
    </div>

    <div v-loading="loading">
      <div v-if="services.length" class="grid">
        <el-card
          v-for="svc in services"
          :key="svc.id"
          shadow="hover"
          class="svc-card"
        >
          <template #header>
            <div class="svc-head">
              <div class="svc-title">
                <el-icon :size="18"><Link /></el-icon>
                <span class="mono">{{ svc.id }}</span>
              </div>
              <div class="svc-actions">
                <el-button link type="primary" @click="openEdit(svc)">
                  <el-icon><Edit /></el-icon>
                  <span>编辑</span>
                </el-button>
                <el-button link type="danger" @click="onDelete(svc)">
                  <el-icon><Delete /></el-icon>
                  <span>删除</span>
                </el-button>
              </div>
            </div>
          </template>

          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="Base URL">
              <span class="mono">{{ svc.base_url }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="API Key">
              <span class="mono">{{ svc.api_key_masked }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">
              <span class="mono muted">{{ fmtDate(svc.created_at) }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="roles">
            <div class="roles-title">模型绑定</div>
            <div class="role-grid">
              <div
                v-for="role in ROLE_ORDER"
                :key="role"
                class="role-item"
                :class="{ bound: !!svc.models[role] }"
              >
                <div class="role-label">
                  <span class="dot" />
                  <span>{{ ROLE_LABELS[role] }}</span>
                </div>
                <div class="role-value">
                  <span v-if="svc.models[role]" class="mono">{{ svc.models[role] }}</span>
                  <span v-else class="muted">未绑定</span>
                </div>
                <el-button link type="primary" size="small" @click="openBind(svc, role)">
                  {{ svc.models[role] ? '更换' : '绑定' }}
                </el-button>
              </div>
            </div>
          </div>
        </el-card>
      </div>

      <el-empty
        v-else-if="!loading"
        description="尚未配置任何上游服务, 点击右上角『新增服务』开始。"
      />
    </div>

    <!-- Create -->
    <el-dialog v-model="createDialog" title="新增上游服务" width="520px">
      <el-form
        ref="createRef"
        :model="createForm"
        :rules="createRules"
        label-width="100px"
      >
        <el-form-item label="服务 ID" prop="id">
          <el-input
            v-model="createForm.id"
            placeholder="例如: dashscope / openrouter / local"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="Base URL" prop="base_url">
          <el-input
            v-model="createForm.base_url"
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
          />
        </el-form-item>
        <el-form-item label="API Key" prop="api_key">
          <el-input
            v-model="createForm.api_key"
            type="password"
            show-password
            placeholder="sk-..."
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="createSubmitting" @click="onCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- Edit -->
    <el-dialog
      v-model="editDialog"
      :title="`编辑服务: ${editing?.id ?? ''}`"
      width="520px"
    >
      <el-form ref="editRef" :model="editForm" label-width="100px">
        <el-form-item label="Base URL">
          <el-input v-model="editForm.base_url" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="editForm.api_key"
            type="password"
            show-password
            placeholder="留空表示保持不变"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="editSubmitting" @click="onEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- Bind model -->
    <el-dialog
      v-model="bindDialog"
      :title="`绑定模型: ${bindTarget?.id ?? ''}`"
      width="520px"
    >
      <el-form label-width="100px">
        <el-form-item label="角色">
          <el-radio-group v-model="bindForm.model_type">
            <el-radio-button
              v-for="role in ROLE_ORDER"
              :key="role"
              :value="role"
            >
              {{ ROLE_LABELS[role] }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="当前绑定">
          <span v-if="bindCurrent" class="mono">{{ bindCurrent }}</span>
          <span v-else class="muted">未绑定</span>
        </el-form-item>
        <el-form-item label="模型名">
          <div class="model-input">
            <el-select
              v-model="bindForm.model"
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
            <el-button
              :loading="availableLoading"
              :disabled="!bindTarget"
              @click="bindTarget && fetchAvailable(bindTarget.id)"
            >
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bindDialog = false">取消</el-button>
        <el-button type="primary" :loading="bindSubmitting" @click="onBind">
          绑定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
}

.head-actions {
  display: flex;
  gap: $space-2;
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(lg) {
    grid-template-columns: repeat(2, 1fr);
  }
}

.svc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.svc-title {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-weight: 600;
}

.svc-actions {
  display: flex;
  gap: $space-1;
}

.roles {
  margin-top: $space-4;
}

.roles-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: $space-2;
}

.role-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-2;
}

.role-item {
  display: grid;
  grid-template-columns: 110px 1fr auto;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);

  &.bound {
    border-color: var(--el-color-primary-light-5);
    background: var(--el-color-primary-light-9);

    .dot {
      background: var(--el-color-primary);
    }
  }
}

.role-label {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-size: 13px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-border-color);
}

.role-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.model-input {
  display: flex;
  gap: $space-2;
  width: 100%;
}
</style>
