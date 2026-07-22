<script setup lang="ts">
import { computed } from 'vue'
import type { ConversationTurn } from '@/types/api'

const props = defineProps<{
  items: ConversationTurn[]
  loading?: boolean
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
  roleFilter?: string[]
  sourceFilter?: string[]
  selectedIds?: number[]
  sources?: Array<{ text: string; value: string }>
}>()

const emit = defineEmits<{
  delete: [row: ConversationTurn]
  deleteSelected: [ids: number[]]
  openDetail: [row: ConversationTurn]
  pageChange: [page: number]
  pageSizeChange: [size: number]
  sortChange: [evt: { prop: string | null; order: string | null }]
  filterChange: [filters: Record<string, unknown[]>]
  select: [id: number, selected: boolean]
  selectAll: [selected: boolean]
}>()

function handleSortChange(evt: { prop: string | null; order: string | null }) {
  emit('sortChange', evt)
}

function handleFilterChange(filters: Record<string, unknown[]>) {
  emit('filterChange', filters)
}

function handleSelectAll(val: boolean | string | number) {
  emit('selectAll', !!val)
}

function handleSelect(id: number, val: boolean | string | number) {
  emit('select', id, !!val)
}

const TURN_ROLE_FILTERS = [
  { text: '用户', value: 'user' },
  { text: '助手', value: 'assistant' },
]

function normalizeOrder(order: string | null | undefined): 'asc' | 'desc' | null {
  if (order === 'ascending') return 'asc'
  if (order === 'descending') return 'desc'
  return null
}

function toElOrder(o: 'asc' | 'desc'): 'ascending' | 'descending' {
  return o === 'asc' ? 'ascending' : 'descending'
}

function roleTag(r: string): 'primary' | 'success' | 'info' {
  if (r === 'user') return 'primary'
  if (r === 'assistant') return 'success'
  return 'info'
}

function roleLabel(r: string): string {
  if (r === 'user') return '用户'
  if (r === 'assistant') return '助手'
  return r
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
</script>

<template>
  <el-table
    v-loading="loading"
    :data="items"
    stripe
    row-key="id"
    empty-text="暂无对话流水"
    :default-sort="{ prop: sortBy, order: toElOrder(sortOrder || 'desc') }"
    @sort-change="handleSortChange"
    @filter-change="handleFilterChange"
    max-height="calc(100vh - 350px)"
  >
    <el-table-column
      label="角色"
      width="120"
      align="center"
      prop="role"
      column-key="role"
      sortable="custom"
      :filters="TURN_ROLE_FILTERS"
      :filter-multiple="false"
      :filtered-value="roleFilter"
    >
      <template #default="{ row }">
        <el-tag :type="roleTag(row.role)" size="small">
          {{ roleLabel(row.role) }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="内容" min-width="360">
      <template #default="{ row }">
        <el-tooltip :content="row.content" placement="top" :disabled="!row.content">
          <div class="mem-content-preview" @click="$emit('openDetail', row)">
            {{ contentPreview(row.content) }}
          </div>
        </el-tooltip>
      </template>
    </el-table-column>
    <el-table-column
      prop="token_count"
      label="token"
      width="100"
      align="center"
      sortable="custom"
    />
    <el-table-column
      label="来源"
      width="140"
      prop="source_frontend"
      column-key="source_frontend"
      sortable="custom"
      :filters="sources"
      :filter-multiple="false"
      :filtered-value="sourceFilter"
    >
      <template #default="{ row }">
        <span class="mono muted">{{ row.source_frontend || '—' }}</span>
      </template>
    </el-table-column>
    <el-table-column
      label="时间"
      width="180"
      prop="ts"
      sortable="custom"
    >
      <template #default="{ row }">
        <span class="mono muted">{{ fmtDate(row.ts) }}</span>
      </template>
    </el-table-column>
    <el-table-column
      label="ID"
      width="90"
      align="center"
      prop="id"
      sortable="custom"
    >
      <template #default="{ row }">
        <span class="mono mini">#{{ row.id }}</span>
      </template>
    </el-table-column>
    <el-table-column width="55" align="center" fixed="right">
      <template #header>
        <el-checkbox
          :model-value="selectedIds && selectedIds.length === items.length && items.length > 0"
          :indeterminate="selectedIds && selectedIds.length > 0 && selectedIds.length < items.length"
          @change="handleSelectAll"
        />
      </template>
      <template #default="{ row }">
        <el-checkbox
          :model-value="selectedIds?.includes(row.id)"
          @change="(val: boolean | string | number) => handleSelect(row.id, val)"
        />
      </template>
    </el-table-column>
  </el-table>
</template>

<style lang="scss" scoped>
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
</style>
