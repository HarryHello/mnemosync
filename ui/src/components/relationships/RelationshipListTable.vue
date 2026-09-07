<script setup lang="ts">
/**
 * 关系列表展示表格 (v0.3.0 多用户).
 *
 * 只负责展示, 不调用 API. 通过 emit 向上传递用户操作.
 */
import type { Relationship } from '@/types/api'
import { formatDate } from '@/utils/format'

defineProps<{
  items: Relationship[]
  loading?: boolean
  total: number
  page: number
  pageSize: number
}>()

const emit = defineEmits<{
  select: [row: Relationship]
  'update:page': [value: number]
  'update:pageSize': [value: number]
  'update:sortBy': [value: string]
  'update:sortOrder': [value: string]
  refresh: []
}>()

type TagType = 'info' | 'primary' | 'success' | 'warning' | 'danger'

function typeTag(t: string | null): { label: string; type: TagType } {
  // 档位与措辞对齐后端 RELATIONSHIP_STAGE_LABELS (models.py) 与详情面板 levelText
  switch (t) {
    case 'intimate':
      return { label: '亲密', type: 'success' }
    case 'friend':
      return { label: '友好', type: 'primary' }
    case 'acquaintance':
      return { label: '熟悉', type: 'success' }
    case 'stranger':
      return { label: '普通', type: 'info' }
    case 'cold':
      return { label: '冷淡', type: 'warning' }
    case 'hostile':
      return { label: '敌对', type: 'danger' }
    default:
      return { label: t ?? '—', type: 'info' }
  }
}

function primaryAccount(row: Relationship) {
  return row.identity?.accounts[0] ?? null
}

function identityName(row: Relationship): string {
  const account = primaryAccount(row)
  return row.identity?.name || account?.display_name || account?.external_key || row.user_id
}

function identityDetail(row: Relationship): string {
  const accounts = row.identity?.accounts ?? []
  if (accounts.length === 0) return row.user_id
  return accounts
    .map((account) => `${account.frontend} · ${account.external_key}`)
    .join(' / ')
}

function clamp(v: number, min: number, max: number): number {
  if (Number.isNaN(v)) return 0
  return Math.max(min, Math.min(max, v))
}

function favorScore(row: Relationship): number {
  return Math.round(clamp(row.favor, -1, 1) * 100)
}
</script>

<template>
  <el-card class="rel-table-card">
    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="user_id"
      empty-text="暂无关系记录 (对话后会自动创建)"
      @row-click="(row: Relationship) => emit('select', row)"
    >
      <el-table-column label="用户" min-width="240" show-overflow-tooltip>
        <template #default="{ row }: { row: Relationship }">
          <div class="identity-cell">
            <span class="identity-name">{{ identityName(row) }}</span>
            <span class="identity-detail mono">{{ identityDetail(row) }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="关系类型" width="100">
        <template #default="{ row }: { row: Relationship }">
          <el-tag size="small" :type="typeTag(row.relationship_type).type">
            {{ typeTag(row.relationship_type).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="好感度" width="120" sortable="custom" prop="favor">
        <template #default="{ row }: { row: Relationship }">
          <span class="mono">{{ favorScore(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="用户称呼" width="140" show-overflow-tooltip>
        <template #default="{ row }: { row: Relationship }">
          {{ row.user_addressing }}
        </template>
      </el-table-column>
      <el-table-column label="最近活跃" width="170">
        <template #default="{ row }: { row: Relationship }">
          <span class="mono">{{ formatDate(row.updated_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="备注" min-width="160" show-overflow-tooltip>
        <template #default="{ row }: { row: Relationship }">
          <span v-if="row.notes" class="muted">{{ row.notes }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="total > pageSize" class="pagination-wrap">
      <el-pagination
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="(v: number) => emit('update:page', v)"
        @size-change="(v: number) => emit('update:pageSize', v)"
      />
    </div>
  </el-card>
</template>

<style lang="scss" scoped>
.identity-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.identity-name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}

.identity-detail {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: $space-4;
}

.mono {
  font-family: var(--el-font-family-mono);
}
</style>