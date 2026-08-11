<script setup lang="ts">
import { ref, watch } from 'vue'
import { VueDraggable } from 'vue-draggable-plus'
import { Plus, Delete, Edit } from '@element-plus/icons-vue'
import RoleCardShell from './RoleCardShell.vue'
import type { RoleBindingItem, UpstreamModelType } from '@/types/api'

const props = defineProps<{
  role: UpstreamModelType
  title: string
  desc: string
  items: RoleBindingItem[]
  servicesEmpty: boolean
}>()

const emit = defineEmits<{
  add: [role: UpstreamModelType]
  reorder: [role: UpstreamModelType, ordered: RoleBindingItem[]]
  remove: [item: RoleBindingItem]
  edit: [item: RoleBindingItem]
}>()

const localItems = ref<RoleBindingItem[]>([...props.items])

watch(
  () => props.items,
  (next) => {
    localItems.value = [...next]
  },
  { deep: false },
)

function onEnd() {
  emit('reorder', props.role, [...localItems.value])
}

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
    :badge-text="`${localItems.length} 个候选`"
  >
    <template #actions>
      <el-button
        type="primary"
        size="small"
        :disabled="servicesEmpty"
        @click="emit('add', role)"
      >
        <el-icon><Plus /></el-icon>
        <span>添加候选</span>
      </el-button>
    </template>

    <div v-if="localItems.length === 0" class="empty">
      <el-empty :image-size="60" description="尚无候选" />
    </div>
    <VueDraggable
      v-else
      v-model="localItems"
      :animation="150"
      handle=".drag-handle"
      class="cand-list"
      @end="onEnd"
    >
      <div
        v-for="(item, idx) in localItems"
        :key="`${item.service_id}::${item.model}`"
        class="cand-row"
      >
        <span class="drag-handle" title="拖动排序">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <rect x="2" y="3"  width="12" height="1.5" rx="0.75" />
            <rect x="2" y="7.25" width="12" height="1.5" rx="0.75" />
            <rect x="2" y="11.5" width="12" height="1.5" rx="0.75" />
          </svg>
        </span>
        <div class="cand-body">
          <div class="cand-model mono">{{ item.model }}</div>
          <div class="cand-svc muted mono">via {{ item.service_id }}</div>
          <div v-if="item.context_length" class="cand-meta">
            <span class="meta-chip">ctx {{ formatContext(item.context_length) }}</span>
          </div>
        </div>
        <div class="cand-actions">
          <span class="cand-prio" :class="{ top: idx === 0 }">#{{ idx }}</span>
          <el-button link size="small" @click="emit('edit', item)">
            <el-icon><Edit /></el-icon>
          </el-button>
          <el-button link type="danger" size="small" @click="emit('remove', item)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
      </div>
    </VueDraggable>
  </RoleCardShell>
</template>

<style lang="scss" scoped>
.empty {
  padding: $space-2 0;
}

.cand-list {
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.cand-row {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  gap: $space-3;
  align-items: center;
  padding: $space-2 $space-3;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-placeholder);
  cursor: grab;

  svg {
    display: block;
    fill: currentcolor;
  }

  &:active {
    cursor: grabbing;
  }

  &:hover {
    color: var(--el-text-color-secondary);
  }
}

.cand-prio {
  margin-right: $space-4;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-secondary);

  &.top {
    color: var(--el-color-primary);
  }
}

.cand-body {
  min-width: 0;
}

.cand-model {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  white-space: nowrap;
}

.cand-svc {
  margin-top: 2px;
  font-size: 12px;
}

.cand-meta {
  display: flex;
  flex-wrap: wrap;
  gap: $space-1;
  margin-top: $space-1;
}

.meta-chip {
  padding: 1px 6px;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
  border-radius: $radius-sm;
}

.cand-actions {
  display: flex;
  gap: $space-1;
  align-items: center;
}

.muted {
  color: var(--el-text-color-secondary);
}

:deep(.sortable-ghost) {
  background: var(--el-color-primary-light-9);
  opacity: 0.4;
}

:deep(.sortable-drag) {
  cursor: grabbing;
}
</style>
