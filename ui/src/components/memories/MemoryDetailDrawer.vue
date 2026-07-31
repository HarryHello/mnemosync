<script setup lang="ts">
import { computed } from 'vue'
import type { Memory } from '@/types/api'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  item: Memory | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

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
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="item ? `记忆 #${item.id}` : '详情'"
    size="50%"
  >
    <div v-if="item" class="detail-content">
      <div class="detail-meta">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="类型">
            <el-tag :type="typeTag(item.memory_type)" size="small">
              {{ typeLabel(item.memory_type) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="来源">
            <span class="mono muted">{{ item.source_user || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="重要度">
            {{ (item.importance * 100).toFixed(0) }}%
          </el-descriptions-item>
          <el-descriptions-item label="衰减率">
            {{ item.decay_rate.toFixed(2) }}
          </el-descriptions-item>
          <el-descriptions-item label="访问次数">
            {{ item.access_count }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            <span class="mono muted">{{ fmtDate(item.created_at) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="最近访问" :span="2">
            <span class="mono muted">{{ fmtDate(item.last_accessed_at) }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </div>
      <div class="detail-text">
        <div class="detail-text-label">内容</div>
        <pre class="detail-pre">{{ item.content }}</pre>
      </div>
    </div>
  </el-drawer>
</template>

<style lang="scss" scoped>
.detail-content {
  display: flex;
  flex-direction: column;
  gap: $space-5;
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
  padding: $space-4;
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

.muted {
  color: var(--el-text-color-secondary);
}
</style>
