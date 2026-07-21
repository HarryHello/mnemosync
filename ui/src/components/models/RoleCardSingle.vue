<script setup lang="ts">
import { Plus, Refresh, Delete } from '@element-plus/icons-vue'
import RoleCardShell from './RoleCardShell.vue'
import type { RoleBindingItem, UpstreamModelType } from '@/types/api'

const props = defineProps<{
  role: UpstreamModelType
  title: string
  desc: string
  item: RoleBindingItem | null
  servicesEmpty: boolean
}>()

const emit = defineEmits<{
  add: [role: UpstreamModelType]
  replace: []
  remove: [item: RoleBindingItem]
}>()

function formatContext(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return String(n)
}
</script>

<template>
  <RoleCardShell
    :title="title"
    :desc="desc"
    badge-text="单绑定"
    badge-tone="warn"
  >
    <template #actions>
      <el-button
        v-if="!item"
        type="primary"
        size="small"
        :disabled="servicesEmpty"
        @click="emit('add', role)"
      >
        <el-icon><Plus /></el-icon>
        <span>添加嵌入模型</span>
      </el-button>
      <el-button
        v-else
        type="warning"
        size="small"
        @click="emit('replace')"
      >
        <el-icon><Refresh /></el-icon>
        <span>替换</span>
      </el-button>
    </template>

    <div v-if="!item" class="empty">
      <el-empty :image-size="60" description="尚未配置嵌入模型" />
    </div>
    <div v-else class="single-binding">
      <div class="cand-body">
        <div class="cand-model mono">{{ item.model }}</div>
        <div class="cand-svc muted mono">via {{ item.service_id }}</div>
        <div class="cand-meta">
          <span class="meta-chip">
            ctx {{ formatContext(item.context_length) }}
          </span>
          <span class="meta-chip">
            dim {{ item.embedding_dim ?? '—' }}
          </span>
          <span
            v-if="item.send_dimensions"
            class="meta-chip meta-chip-warn"
            title="dimensions 会被透传给上游 (可变维模型才需要)"
          >
            send-dim
          </span>
        </div>
      </div>
      <el-button
        link
        type="danger"
        size="small"
        @click="emit('remove', item)"
      >
        <el-icon><Delete /></el-icon>
      </el-button>
    </div>
  </RoleCardShell>
</template>

<style lang="scss" scoped>
.empty {
  padding: $space-2 0;
}

.single-binding {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: $space-3;
  padding: $space-2 $space-3;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
}

.cand-body {
  min-width: 0;
}

.cand-model {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cand-svc {
  font-size: 12px;
  margin-top: 2px;
}

.cand-meta {
  display: flex;
  gap: $space-1;
  margin-top: $space-1;
  flex-wrap: wrap;
}

.meta-chip {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: $radius-sm;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}

.meta-chip-warn {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
