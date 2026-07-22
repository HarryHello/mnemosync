<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { Filter, Search } from '@element-plus/icons-vue'
import type { HttpLog } from '@/types/api'

const props = defineProps<{
  items: HttpLog[]
  total: number
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:page': [value: number]
  'update:pageSize': [value: number]
  'open': [row: HttpLog]
  'filter': [filters: { method?: string; status?: number; path?: string; since?: string; until?: string }]
}>()

const page = ref(1)
const pageSize = ref(20)

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const STATUS_OPTIONS = [
  { text: '200', value: 200 },
  { text: '201', value: 201 },
  { text: '400', value: 400 },
  { text: '401', value: 401 },
  { text: '403', value: 403 },
  { text: '404', value: 404 },
  { text: '422', value: 422 },
  { text: '500', value: 500 },
]

const filterStates = reactive<{
  method: string | null
  status: string | null
  path: string
  since: string
  until: string
}>({
  method: null,
  status: null,
  path: '',
  since: '',
  until: '',
})

const pathFilterVisible = ref(false)
const pathHeaderRef = ref<HTMLElement>()
const timeFilterVisible = ref(false)
const timeHeaderRef = ref<HTMLElement>()

watch(page, (v) => emit('update:page', v))
watch(pageSize, (v) => emit('update:pageSize', v))

function onFilterChange(filters: Record<string, string[]>) {
  page.value = 1
  emit('filter', {
    method: filters.method?.[0],
    status: filters.status?.[0] ? Number(filters.status[0]) : undefined,
  })
}

function applyPathFilter() {
  page.value = 1
  pathFilterVisible.value = false
  emit('filter', { path: filterStates.path.trim() || undefined })
}

function clearPathFilter() {
  filterStates.path = ''
  page.value = 1
  pathFilterVisible.value = false
  emit('filter', { path: undefined })
}

function applyTimeFilter() {
  page.value = 1
  timeFilterVisible.value = false
  emit('filter', {
    since: filterStates.since || undefined,
    until: filterStates.until || undefined,
  })
}

function clearTimeFilter() {
  filterStates.since = ''
  filterStates.until = ''
  page.value = 1
  timeFilterVisible.value = false
  emit('filter', {
    since: undefined,
    until: undefined,
  })
}

function onOpen(row: HttpLog) {
  emit('open', row)
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
</script>

<template>
  <el-card shadow="never">
    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="id"
      empty-text="暂无日志"
      @row-click="onOpen"
      @filter-change="onFilterChange"
      max-height="calc(100vh - 210px)"
    >
      <el-table-column label="时间" width="190">
        <template #header>
          <div ref="timeHeaderRef" class="time-header">
            <span>时间</span>
            <el-icon class="filter-icon" @click.stop="timeFilterVisible = !timeFilterVisible">
              <Filter v-if="!filterStates.since && !filterStates.until" />
              <Search v-else />
            </el-icon>
          </div>
        </template>
        <template #default="{ row }">
          <span class="mono muted">{{ fmtDate(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column
        label="方法"
        column-key="method"
        width="90"
        align="center"
        :filters="METHODS.map((m) => ({ text: m, value: m }))"
      >
        <template #default="{ row }">
          <el-tag size="small" effect="plain" class="mono">{{ row.method }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="路径" min-width="280">
        <template #header>
          <div ref="pathHeaderRef" class="path-header">
            <span>路径</span>
            <el-icon class="filter-icon" @click.stop="pathFilterVisible = !pathFilterVisible">
              <Filter v-if="!filterStates.path" />
              <Search v-else />
            </el-icon>
          </div>
        </template>
        <template #default="{ row }">
          <span class="mono">{{ row.path }}</span>
        </template>
      </el-table-column>
      <el-table-column
        label="状态"
        column-key="status"
        width="100"
        align="center"
        :filters="STATUS_OPTIONS"
      >
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

  <el-popover
    v-model:visible="pathFilterVisible"
    placement="bottom"
    :virtual-ref="pathHeaderRef"
    virtual-triggering
    trigger="manual"
    width="300"
    :persistent="true"
  >
    <el-input
      v-model="filterStates.path"
      placeholder="输入路径包含的内容"
      clearable
      @keyup.enter="applyPathFilter"
    >
      <template #append>
        <el-button @click="applyPathFilter">确定</el-button>
      </template>
    </el-input>
    <template #reference />
  </el-popover>

  <el-popover
    v-model:visible="timeFilterVisible"
    placement="bottom"
    :virtual-ref="timeHeaderRef"
    virtual-triggering
    trigger="manual"
    width="320"
    :persistent="true"
  >
    <div class="time-filter-content">
      <div class="time-filter-item">
        <span class="time-filter-label">开始时间</span>
        <el-date-picker
          v-model="filterStates.since"
          type="datetime"
          placeholder="选择开始时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss"
          :teleported="false"
        />
      </div>
      <div class="time-filter-item">
        <span class="time-filter-label">结束时间</span>
        <el-date-picker
          v-model="filterStates.until"
          type="datetime"
          placeholder="选择结束时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss"
          :teleported="false"
        />
      </div>
      <div class="time-filter-actions">
        <el-button size="small" @click="clearTimeFilter">清除</el-button>
        <el-button type="primary" size="small" @click="applyTimeFilter">确定</el-button>
      </div>
    </div>
    <template #reference />
  </el-popover>
</template>

<style lang="scss" scoped>
.path-header,
.time-header {
  display: flex;
  align-items: center;
  gap: $space-1;
}

.filter-icon {
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-secondary);

  &:hover {
    color: var(--el-color-primary);
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

.time-filter-content {
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.time-filter-item {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.time-filter-label {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  min-width: 60px;
}

.time-filter-actions {
  display: flex;
  justify-content: flex-end;
  gap: $space-2;
  margin-top: $space-1;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
