<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listLogs, getLog, clearLogs } from '@/api/client'
import type { HttpLog } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import LogsTable from '@/components/request-logs/LogsTable.vue'
import LogDetailDrawer from '@/components/request-logs/LogDetailDrawer.vue'

const rows = ref<HttpLog[]>([])
const total = ref(0)
const loading = ref(false)

const currentFilters = ref<{
  method?: string
  status?: number
  path?: string
  since?: string
  until?: string
}>({})
const page = ref(1)
const pageSize = ref(20)

const drawer = ref(false)
const detailLoading = ref(false)
const detail = ref<HttpLog | null>(null)

async function refresh() {
  loading.value = true
  try {
    const res = await listLogs({
      page: page.value,
      page_size: pageSize.value,
      ...currentFilters.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function onFilter(filters: { method?: string; status?: number; path?: string }) {
  currentFilters.value = { ...currentFilters.value, ...filters }
  refresh()
}

async function onOpen(row: HttpLog) {
  drawer.value = true
  detail.value = null
  detailLoading.value = true
  try {
    detail.value = await getLog(row.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    drawer.value = false
  } finally {
    detailLoading.value = false
  }
}

async function onClearAll() {
  try {
    await ElMessageBox.confirm(
      `将删除全部 ${total.value} 条日志, 且不可恢复。确认继续？`,
      '清空日志',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await clearLogs()
    ElMessage.success('已清空')
    page.value = 1
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)

watch(page, () => refresh())
watch(pageSize, () => { page.value = 1; refresh() })
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="请求日志"
      subtitle="由 HTTP 中间件异步写入, 记录所有 /panel/ 与 /v1/ 请求的入参、响应与耗时。"
    >
      <template #actions>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="danger" :disabled="total === 0" @click="onClearAll">
          <el-icon><Delete /></el-icon>
          <span>清空</span>
        </el-button>
      </template>
    </PageHeader>

    <LogsTable
      :items="rows"
      :total="total"
      :loading="loading"
      @update:page="page = $event"
      @update:pageSize="pageSize = $event"
      @open="onOpen"
      @filter="onFilter"
    />

    <LogDetailDrawer
      v-model="drawer"
      :detail="detail"
      :detail-loading="detailLoading"
    />
  </div>
</template>
