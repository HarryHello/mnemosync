<script setup lang="ts">
import { computed } from 'vue'
import type { Memory } from '@/types/api'

const props = defineProps<{
  items: Memory[]
  loading?: boolean
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
  typeFilter?: string[]
}>()

const emit = defineEmits<{
  delete: [row: Memory]
  openDetail: [row: Memory]
  sortChange: [evt: { prop: string | null; order: string | null }]
  filterChange: [filters: Record<string, unknown[]>]
}>()

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
</script>

<template>
  <el-table
    v-loading="loading"
    :data="items"
    stripe
    row-key="id"
    empty-text="暂无记忆"
    :default-sort="{ prop: sortBy, order: toElOrder(sortOrder || 'desc') }"
    @sort-change="(evt: { prop: string | null; order: string | null }) => emit('sortChange', evt)"
    @filter-change="(filters: Record<string, unknown[]>) => emit('filterChange', filters)"
    max-height="calc(100vh - 340px)"
  >
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
        <el-button link type="danger" @click="$emit('delete', row)">删除</el-button>
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
