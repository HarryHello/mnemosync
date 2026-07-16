<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { listApiKeys, createApiKey, deleteApiKey } from '@/api/client'
import type { ApiKeyCreateResponse, ApiKeyInfo } from '@/types/api'

const keys = ref<ApiKeyInfo[]>([])
const loading = ref(false)

const createDialog = ref(false)
const createForm = reactive({ note: '' })
const createRef = ref<FormInstance | null>(null)
const createSubmitting = ref(false)

const rules: FormRules = {
  note: [{ required: true, message: '请填写用途备注', trigger: 'blur' }],
}

const secretDialog = ref(false)
const newKey = ref<ApiKeyCreateResponse | null>(null)

const total = computed(() => keys.value.length)

async function refresh() {
  loading.value = true
  try {
    const res = await listApiKeys()
    keys.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.note = ''
  createDialog.value = true
}

async function onCreate() {
  if (!createRef.value) return
  const ok = await createRef.value.validate().catch(() => false)
  if (!ok) return
  createSubmitting.value = true
  try {
    const created = await createApiKey({ note: createForm.note })
    createDialog.value = false
    newKey.value = created
    secretDialog.value = true
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    createSubmitting.value = false
  }
}

async function copyKey(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('剪贴板不可用, 请手动选中复制')
  }
}

async function onRevoke(row: ApiKeyInfo) {
  try {
    await ElMessageBox.confirm(
      `撤销后使用该 Key 的客户端将立即失效, 无法恢复。确认撤销 "${row.note || row.key_prefix}" ?`,
      '撤销 API Key',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteApiKey(row.id)
    ElMessage.success('已撤销')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">API Key</h2>
        <p class="page-subtitle">
          管理第三方客户端接入 Mnemosync 的密钥。完整 Key 仅在创建时显示一次, 请立即复制保存。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>创建 Key</span>
        </el-button>
      </div>
    </div>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>密钥列表</span>
          <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
        </div>
      </template>
      <el-table
        v-loading="loading"
        :data="keys"
        stripe
        row-key="id"
        empty-text="暂无 API Key"
      >
        <el-table-column label="Key 前缀" min-width="200">
          <template #default="{ row }">
            <span class="mono">{{ row.key_prefix }}…</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">启用</el-tag>
            <el-tag v-else type="info" size="small">已撤销</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近使用" width="180">
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.last_used_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="danger"
              :disabled="!row.is_active"
              @click="onRevoke(row)"
            >
              撤销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createDialog" title="创建 API Key" width="480px">
      <el-form
        ref="createRef"
        :model="createForm"
        :rules="rules"
        label-width="80px"
      >
        <el-form-item label="备注" prop="note">
          <el-input
            v-model="createForm.note"
            placeholder="例如: Cursor / Cherry Studio / 我的桌面客户端"
            maxlength="128"
            show-word-limit
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

    <el-dialog
      v-model="secretDialog"
      title="Key 创建成功"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-alert type="warning" :closable="false" show-icon>
        <template #title>
          完整 Key <b>仅显示一次</b>, 关闭后无法再次查看。请立即复制保存。
        </template>
      </el-alert>
      <div v-if="newKey" class="secret-block">
        <div class="secret-label">API Key</div>
        <div class="secret-value">
          <span class="mono">{{ newKey.key }}</span>
          <el-button size="small" @click="copyKey(newKey.key)">
            <el-icon><CopyDocument /></el-icon>
            <span>复制</span>
          </el-button>
        </div>
        <div class="secret-label">备注</div>
        <div class="secret-note">{{ newKey.note }}</div>
      </div>
      <template #footer>
        <el-button type="primary" @click="secretDialog = false">我已保存</el-button>
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

.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.muted {
  color: var(--el-text-color-secondary);
}

.secret-block {
  margin-top: $space-4;
}

.secret-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: $space-1;
}

.secret-value {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  border-radius: $radius-sm;
  background: var(--el-fill-color-light);
  margin-bottom: $space-3;
  word-break: break-all;

  .mono {
    flex: 1;
    font-size: 13px;
  }
}

.secret-note {
  color: var(--el-text-color-primary);
}
</style>
