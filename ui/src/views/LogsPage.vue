<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listLogs, getLog, clearLogs } from '@/api/client'
import type { HttpLog } from '@/types/api'

const rows = ref<HttpLog[]>([])
const total = ref(0)
const loading = ref(false)

const filters = reactive({
  method: '',
  path: '',
  status: '' as string | number,
})
const page = ref(1)
const pageSize = ref(20)

const drawer = ref(false)
const detailLoading = ref(false)
const detail = ref<HttpLog | null>(null)

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']

const filtersActive = computed(
  () => !!filters.method || !!filters.path.trim() || filters.status !== '',
)

async function refresh() {
  loading.value = true
  try {
    const status =
      filters.status === '' ? undefined : Number(filters.status) || undefined
    const res = await listLogs({
      page: page.value,
      page_size: pageSize.value,
      method: filters.method || undefined,
      path: filters.path.trim() || undefined,
      status,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.method = ''
  filters.path = ''
  filters.status = ''
  page.value = 1
  refresh()
}

function onSearch() {
  page.value = 1
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

function statusType(code: number | null): 'success' | 'warning' | 'danger' | 'info' {
  if (code == null) return 'info'
  if (code >= 500) return 'danger'
  if (code >= 400) return 'warning'
  if (code >= 200) return 'success'
  return 'info'
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms.toFixed(1)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s + 'Z').toLocaleString('zh-CN', { hour12: false })
}

function pretty(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

watch([page, pageSize], refresh)

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">请求日志</h2>
        <p class="page-subtitle">
          由 HTTP 中间件异步写入, 记录所有 <span class="mono">/panel/</span> 与
          <span class="mono">/v1/</span> 请求的入参、响应与耗时。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="danger" :disabled="total === 0" @click="onClearAll">
          <el-icon><Delete /></el-icon>
          <span>清空</span>
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="filters">
      <el-form :inline="true" @submit.prevent="onSearch">
        <el-form-item label="方法">
          <el-select
            v-model="filters.method"
            placeholder="全部"
            clearable
            style="width: 120px"
          >
            <el-option v-for="m in METHODS" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态码">
          <el-input
            v-model="filters.status"
            placeholder="如 200 / 500"
            clearable
            style="width: 140px"
          />
        </el-form-item>
        <el-form-item label="路径包含">
          <el-input
            v-model="filters.path"
            placeholder="如 /v1/chat"
            clearable
            style="width: 220px"
            @keyup.enter="onSearch"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">
            <el-icon><Search /></el-icon>
            <span>搜索</span>
          </el-button>
          <el-button :disabled="!filtersActive" @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="rows"
        stripe
        row-key="id"
        empty-text="暂无日志"
        @row-click="onOpen"
      >
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方法" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" class="mono">{{ row.method }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="路径" min-width="280">
          <template #default="{ row }">
            <span class="mono">{{ row.path }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.response_status)" size="small">
              {{ row.response_status ?? '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="110" align="right">
          <template #default="{ row }">
            <span class="mono">{{ fmtDuration(row.duration_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="客户端" width="140">
          <template #default="{ row }">
            <span class="mono muted">{{ row.client_ip || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="right" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="onOpen(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
        />
      </div>
    </el-card>

    <el-drawer v-model="drawer" title="日志详情" size="640px" direction="rtl">
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="时间">
              <span class="mono">{{ fmtDate(detail.created_at) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="方法">
              <el-tag size="small" class="mono">{{ detail.method }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="路径">
              <span class="mono">{{ detail.path }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="Query">
              <span class="mono">{{ detail.query_params || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="状态码">
              <el-tag :type="statusType(detail.response_status)" size="small">
                {{ detail.response_status ?? '—' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="耗时">
              <span class="mono">{{ fmtDuration(detail.duration_ms) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="客户端">
              <span class="mono">{{ detail.client_ip || '—' }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="section-title">请求头</div>
          <pre class="code-block">{{ pretty(detail.request_headers) }}</pre>

          <div class="section-title">请求体</div>
          <pre class="code-block">{{ pretty(detail.request_body) || '—' }}</pre>

          <div class="section-title">响应体</div>
          <pre class="code-block">{{ pretty(detail.response_body) || '—' }}</pre>
        </template>
      </div>
    </el-drawer>
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

.filters {
  margin-bottom: $space-4;

  :deep(.el-form-item) {
    margin-bottom: 0;
  }
}

.muted {
  color: var(--el-text-color-secondary);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: $space-3;
}

.detail {
  padding: 0 $space-2 $space-4;
}

.section-title {
  margin: $space-4 0 $space-2;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.code-block {
  background: var(--el-fill-color-light);
  border-radius: $radius-sm;
  padding: $space-3;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
