<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  listUpstreamServices,
  createUpstreamService,
  updateUpstreamService,
  deleteUpstreamService,
  listUpstreamAvailableModels,
} from '@/api/client'
import { formatDate } from '@/utils/format'
import type { UpstreamService } from '@/types/api'

const services = ref<UpstreamService[]>([])
const loading = ref(false)

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
      `将删除服务 "${row.id}", 该服务所引用的所有模型绑定项也会失效, 需要在『模型管理』tab 重新配置。确认删除？`,
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

async function onTest(row: UpstreamService) {
  try {
    const { models } = await listUpstreamAvailableModels(row.id)
    if (!models.length) {
      ElMessage.success('连接成功, 但未探测到可用模型')
      return
    }
    const list = models.map((m) => `<li class="mono">${m}</li>`).join('')
    await ElMessageBox.alert(
      `<ul style="margin:0;padding-left:18px;max-height:320px;overflow:auto">${list}</ul>`,
      `可用模型 (${row.id})`,
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '关闭',
        customClass: 'test-models-box',
      },
    )
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">上游 API</h3>
        <p class="tab-subtitle">
          管理 Mnemosync 使用的 OpenAI 兼容上游服务商 (如 DashScope), 只维护凭证 (Base URL + API Key)。
          API Key 存储时使用 Fernet 对称加密。要为具体角色 (主 / 辅助 / 嵌入 / 重排) 绑定模型,
          请切换到『模型管理』tab。
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
      <el-card v-if="services.length">
        <el-table :data="services" stripe>
          <el-table-column label="服务 ID" prop="id" min-width="160">
            <template #default="{ row }: { row: UpstreamService }">
              <span class="mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Base URL" prop="base_url" min-width="320">
            <template #default="{ row }: { row: UpstreamService }">
              <span class="mono">{{ row.base_url }}</span>
            </template>
          </el-table-column>
          <el-table-column label="API Key" min-width="180">
            <template #default="{ row }: { row: UpstreamService }">
              <span class="mono muted">{{ row.api_key_masked }}</span>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="180">
            <template #default="{ row }: { row: UpstreamService }">
              <span class="mono muted">{{ formatDate(row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }: { row: UpstreamService }">
              <el-button link type="primary" @click="onTest(row)">
                <el-icon><Connection /></el-icon>
                <span>测试</span>
              </el-button>
              <el-button link type="primary" @click="openEdit(row)">
                <el-icon><Edit /></el-icon>
                <span>编辑</span>
              </el-button>
              <el-button link type="danger" @click="onDelete(row)">
                <el-icon><Delete /></el-icon>
                <span>删除</span>
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

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
            placeholder="https://your-provider.com/v1"
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
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;

}

.tab-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 $space-1;
}

.tab-subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.head-actions {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
