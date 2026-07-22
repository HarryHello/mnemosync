<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory } from '@/api/client'
import type { Memory } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

const props = defineProps<{
  active?: boolean
}>()

const emit = defineEmits<{}>()

const memories = ref<Memory[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const sortBy = ref('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')
const typeFilter = ref<string[]>([])
const sourceUser = ref('default')
const loaded = ref(false)
const detailDrawerVisible = ref(false)
const currentDetailRow = ref<Memory | null>(null)

const MEMORY_TYPE_FILTERS = [
  { text: '普通', value: 'normal' },
  { text: '永久', value: 'permanent' },
]

function normalizeOrder(order: string | null | undefined): 'asc' | 'desc' | null {
  if (order === 'ascending') return 'asc'
  if (order === 'descending') return 'desc'
  return null
}

function toElOrder(o: 'asc' | 'desc'): 'ascending' | 'descending' {
  return o === 'asc' ? 'ascending' : 'descending'
}

async function refresh() {
  loading.value = true
  try {
    const res = await listMemories({
      source_user: sourceUser.value || 'default',
      page: page.value,
      page_size: pageSize.value,
      memory_type: typeFilter.value[0] || undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    })
    memories.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onDelete(row: Memory) {
  try {
    await ElMessageBox.confirm(
      `确认删除此记忆？删除后不可恢复。\n\n${row.content.slice(0, 80)}${row.content.length > 80 ? '…' : ''}`,
      '删除记忆',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteMemory(row.id)
    ElMessage.success('已删除')
    if (memories.value.length === 1 && page.value > 1) {
      page.value -= 1
    }
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function onPageChange(p: number) {
  page.value = p
  refresh()
}

function onPageSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  refresh()
}

function onSourceApply() {
  page.value = 1
  refresh()
}

function onSortChange(evt: { prop: string | null; order: string | null }) {
  const order = normalizeOrder(evt.order)
  if (!evt.prop || !order) {
    sortBy.value = 'created_at'
    sortOrder.value = 'desc'
  } else {
    sortBy.value = evt.prop
    sortOrder.value = order
  }
  page.value = 1
  refresh()
}

function onFilterChange(filters: Record<string, unknown[]>) {
  const t = filters['memory_type'] as string[] | undefined
  typeFilter.value = t ?? []
  page.value = 1
  refresh()
}

function typeTag(t: string): 'success' | 'primary' | 'info' {
  if (t === 'permanent') return 'success'
  if (t === 'normal') return 'primary'
  return 'info'
}

function typeLabel(t: string): string {
  if (t === 'permanent') return '永久'
  if (t === 'normal') return '普通'
  return t
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

function contentPreview(content: string): string {
  const firstLine = content.split('\n')[0] || ''
  if (firstLine.length > 100) {
    return firstLine.slice(0, 100) + '…'
  }
  if (content.includes('\n')) {
    return firstLine + '…'
  }
  return content
}

function openDetail(row: Memory): void {
  currentDetailRow.value = row
  detailDrawerVisible.value = true
}

watch(() => props.active, (active) => {
  if (active && !loaded.value) {
    refresh()
    loaded.value = true
  }
}, { immediate: true })
</script>

<template>
  <div class="memories-tab">
    <PageHeader
      title="长期记忆"
      subtitle="按重要度/衰减规则汰换，列头可点击排序 / 过滤。"
    >
      <template #actions>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <span>长期记忆条目</span>
            <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
          </div>
          <div class="header-right">
            <el-form :inline="true" @submit.prevent="onSourceApply">
              <el-form-item label="source_user">
                <el-input
                  v-model="sourceUser"
                  placeholder="default"
                  clearable
                  style="width: 180px"
                  @keyup.enter="onSourceApply"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="onSourceApply">
                  <el-icon><Search /></el-icon>
                  <span>查询</span>
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </div>
      </template>
      <el-table
        v-loading="loading"
        :data="memories"
        stripe
        row-key="id"
        empty-text="暂无记忆"
        :default-sort="{ prop: sortBy, order: toElOrder(sortOrder) }"
        @sort-change="onSortChange"
        @filter-change="onFilterChange"
      >
        <el-table-column label="内容" min-width="360">
          <template #default="{ row }">
            <el-tooltip :content="row.content" placement="top" :disabled="!row.content">
              <div class="mem-content-preview" @click="openDetail(row)">
                {{ contentPreview(row.content) }}
              </div>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          label="类型"
          width="120"
          align="center"
          prop="memory_type"
          column-key="memory_type"
          sortable="custom"
          :filters="MEMORY_TYPE_FILTERS"
          :filter-multiple="false"
          :filtered-value="typeFilter"
        >
          <template #default="{ row }">
            <el-tag :type="typeTag(row.memory_type)" size="small">
              {{ typeLabel(row.memory_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="重要度"
          width="120"
          align="center"
          prop="importance"
          sortable="custom"
        >
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.importance * 100)"
              :stroke-width="6"
              :show-text="false"
            />
            <div class="mono mini">{{ row.importance.toFixed(2) }}</div>
          </template>
        </el-table-column>
        <el-table-column
          label="衰减率"
          width="100"
          align="center"
          prop="decay_rate"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono">{{ row.decay_rate.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="access_count"
          label="访问次数"
          width="110"
          align="center"
          sortable="custom"
        />
        <el-table-column
          label="来源"
          width="120"
          prop="source_user"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono muted">{{ row.source_user || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="创建时间"
          width="180"
          prop="created_at"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="最近访问"
          width="180"
          prop="last_accessed_at"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.last_accessed_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="right" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100, 200]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>

    <el-drawer
      v-model="detailDrawerVisible"
      :title="currentDetailRow ? `记忆 #${currentDetailRow.id}` : '详情'"
      size="50%"
    >
      <div v-if="currentDetailRow" class="detail-content">
        <div class="detail-meta">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="类型">
              <el-tag :type="typeTag(currentDetailRow.memory_type)" size="small">
                {{ typeLabel(currentDetailRow.memory_type) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="来源">
              <span class="mono muted">{{ currentDetailRow.source_user || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="重要度">
              {{ (currentDetailRow.importance * 100).toFixed(0) }}%
            </el-descriptions-item>
            <el-descriptions-item label="衰减率">
              {{ currentDetailRow.decay_rate.toFixed(2) }}
            </el-descriptions-item>
            <el-descriptions-item label="访问次数">
              {{ currentDetailRow.access_count }}
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">
              <span class="mono muted">{{ fmtDate(currentDetailRow.created_at) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="最近访问" :span="2">
              <span class="mono muted">{{ fmtDate(currentDetailRow.last_accessed_at) }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>
        <div class="detail-text">
          <div class="detail-text-label">内容</div>
          <pre class="detail-pre">{{ currentDetailRow.content }}</pre>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-4;
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.header-right {
  :deep(.el-form-item) {
    margin-bottom: 0;
  }
}

.mem-content-preview {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  color: var(--el-text-color-regular);
  line-height: 1.5;

  &:hover {
    color: var(--el-color-primary);
  }
}

.muted {
  color: var(--el-text-color-secondary);
}

.mini {
  font-size: 11px;
  margin-top: 2px;
  color: var(--el-text-color-secondary);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: $space-3;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: $space-4;
}

.detail-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.detail-text-label {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.detail-pre {
  margin: 0;
  padding: $space-3;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-md;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-family: inherit;
  font-size: 14px;
  color: var(--el-text-color-primary);
  max-height: 60vh;
  overflow-y: auto;
}
</style>
