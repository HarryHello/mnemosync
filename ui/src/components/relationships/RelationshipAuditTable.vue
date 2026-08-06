<script setup lang="ts">
import type { RelationshipAuditEntry } from '@/types/api'
import { formatDate } from '@/utils/format'

defineProps<{
  items: RelationshipAuditEntry[]
  loading?: boolean
}>()

defineEmits<{
  refresh: []
  revert: [entry: RelationshipAuditEntry]
}>()

const fieldLabels: Record<string, string> = {
  persona_addressing: '人格自称',
  user_addressing: '用户称呼',
  context: '关系背景',
}

function sourceTagType(src: string): 'primary' | 'success' | 'info' {
  if (src === 'agent') return 'primary'
  if (src === 'manual') return 'success'
  return 'info'
}
</script>

<template>
  <div>
    <el-empty
      v-if="!loading && items.length === 0"
      description="暂无变更"
      :image-size="60"
    />
    <el-table v-else :data="items" size="small" stripe>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">
          <span class="mono">{{ formatDate(row.changed_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="sourceTagType(row.source)">
            {{ row.source }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="字段" width="100">
        <template #default="{ row }">
          {{ fieldLabels[row.field_name] ?? row.field_name }}
        </template>
      </el-table-column>
      <el-table-column label="旧值 → 新值">
        <template #default="{ row }">
          <span class="muted">{{ row.old_value ?? '(空)' }}</span>
          <span class="arrow"> → </span>
          <span>{{ row.new_value ?? '(空)' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="原因" min-width="180">
        <template #default="{ row }">
          <span class="reason">{{ row.reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" align="right">
        <template #default="{ row }">
          <el-button size="small" text type="warning" @click="$emit('revert', row)">
            回退到此
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style lang="scss" scoped>
.muted {
  color: var(--el-text-color-secondary);
}

.arrow {
  color: var(--el-text-color-secondary);
  margin: 0 4px;
}

.reason {
  color: var(--el-text-color-regular);
  word-break: break-word;
}
</style>
