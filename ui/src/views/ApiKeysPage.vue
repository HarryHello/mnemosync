<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listApiKeys, createApiKey, deleteApiKey } from '@/api/client'
import type { ApiKeyCreateResponse, ApiKeyInfo } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import ApiKeyTable from '@/components/api-keys/ApiKeyTable.vue'
import ApiKeyCreateDialog from '@/components/api-keys/ApiKeyCreateDialog.vue'
import ApiKeySecretDialog from '@/components/api-keys/ApiKeySecretDialog.vue'

const keys = ref<ApiKeyInfo[]>([])
const loading = ref(false)
const createDialog = ref(false)
const createSubmitting = ref(false)
const secretDialog = ref(false)
const newKey = ref<ApiKeyCreateResponse | null>(null)

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

async function onCreate(note: string) {
  createSubmitting.value = true
  try {
    const created = await createApiKey({ note })
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

async function onCopyRow(row: ApiKeyInfo) {
  if (!row.key) {
    ElMessage.warning('该 Key 无法读取完整值 (可能为历史数据), 请重新生成')
    return
  }
  await copyKey(row.key)
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

function clearNewKey() {
  newKey.value = null
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="API Key"
      subtitle="管理第三方客户端接入 Mnemosync 的密钥。密钥经 Fernet 加密后存储, 点击表格中的 Key 可随时复制完整值。"
    >
      <template #actions>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="primary" @click="createDialog = true">
          <el-icon><Plus /></el-icon>
          <span>创建 Key</span>
        </el-button>
      </template>
    </PageHeader>

    <ApiKeyTable
      :items="keys"
      :loading="loading"
      @copy="onCopyRow"
      @revoke="onRevoke"
    />

    <ApiKeyCreateDialog
      v-model="createDialog"
      :submitting="createSubmitting"
      @submit="onCreate"
    />

    <ApiKeySecretDialog
      v-model="secretDialog"
      :api-key="newKey"
      @copy="copyKey"
      @closed="clearNewKey"
    />
  </div>
</template>
