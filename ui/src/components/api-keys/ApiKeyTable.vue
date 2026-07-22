<script setup lang="ts">
import { computed } from 'vue'
import type { ApiKeyInfo } from '@/types/api'

const props = defineProps<{
  items: ApiKeyInfo[]
  loading: boolean
}>()

const emit = defineEmits<{
  copy: [item: ApiKeyInfo]
  revoke: [item: ApiKeyInfo]
}>()

const total = computed(() => props.items.length)

function maskKey(item: ApiKeyInfo): string {
  if (item.key && item.key.length >= 10) {
    return `${item.key.slice(0, 4)}******${item.key.slice(-4)}`
  }
  return `${item.key_prefix}…`
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>密钥列表</span>
        <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
      </div>
    </template>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="id"
      empty-text="暂无 API Key"
      max-height="calc(100vh - 210px)"
    >
      <el-table-column label="Key" min-width="220">
        <template #default="{ row }">
          <el-tooltip
            :content="row.key ? '点击复制完整 Key' : '历史数据, 无法读取完整 Key'"
            placement="top"
          >
            <span
              class="key-cell mono"
              :class="{ 'key-cell-disabled': !row.key }"
              @click="emit('copy', row)"
            >
              <span>{{ maskKey(row) }}</span>
              <el-icon v-if="row.key" class="copy-icon"><CopyDocument /></el-icon>
            </span>
          </el-tooltip>
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
          <span class="mono muted">{{ formatDate(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最近使用" width="180">
        <template #default="{ row }">
          <span class="mono muted">{{ formatDate(row.last_used_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" align="right" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="danger"
            :disabled="!row.is_active"
            @click="emit('revoke', row)"
          >
            撤销
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.muted {
  color: var(--el-text-color-secondary);
}

.key-cell {
  display: inline-flex;
  align-items: center;
  gap: $space-1;
  padding: 2px 8px;
  border-radius: $radius-sm;
  cursor: pointer;
  user-select: all;
  transition: background 0.15s;

  &:hover {
    background: var(--el-fill-color-light);
  }
}

.key-cell-disabled {
  cursor: not-allowed;
  color: var(--el-text-color-secondary);

  &:hover {
    background: transparent;
  }
}

.copy-icon {
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
</style>
