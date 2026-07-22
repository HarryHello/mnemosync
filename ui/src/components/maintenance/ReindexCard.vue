<script setup lang="ts">
import { computed } from 'vue'
import type { ReindexStatusResponse } from '@/types/api'

const props = defineProps<{
  status: ReindexStatusResponse
  reindexForm: {
    prune: boolean
    priority_threshold: number
  }
  reindexStarting: boolean
}>()

const emit = defineEmits<{
  'start-reindex': []
  'refresh-status': []
}>()

const progressPct = computed(() => {
  if (!props.status.total) return 0
  return Math.min(100, Math.floor((props.status.processed / props.status.total) * 100))
})

const isRunning = computed(() => props.status.state === 'running')

function fmtTime(s: string | null): string {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleString()
  } catch {
    return s
  }
}
</script>

<template>
  <el-card shadow="hover" class="section equal-card">
    <template #header>
      <div class="sec-head">
        <span class="sec-title">重建记忆向量库 (Reindex)</span>
        <el-tag :type="{
          idle: 'info',
          running: 'warning',
          success: 'success',
          error: 'danger',
        }[status.state]" size="small">
          {{ status.state }}
        </el-tag>
      </div>
    </template>

    <el-descriptions :column="1" size="small" border>
      <el-descriptions-item label="进度">
        <el-progress
          :percentage="progressPct"
          :status="status.state === 'error' ? 'exception' : (status.state === 'success' ? 'success' : undefined)"
        />
        <div class="hint">
          {{ status.processed }} / {{ status.total }} · pruned {{ status.pruned }}
        </div>
      </el-descriptions-item>
      <el-descriptions-item label="开始">
        {{ fmtTime(status.started_at) }}
      </el-descriptions-item>
      <el-descriptions-item label="结束">
        {{ fmtTime(status.finished_at) }}
      </el-descriptions-item>
      <el-descriptions-item v-if="status.error" label="错误">
        <span class="err">{{ status.error }}</span>
      </el-descriptions-item>
    </el-descriptions>

    <el-divider />

    <el-form label-width="120px" size="default">
      <el-form-item label="顺便清理">
        <el-switch v-model="reindexForm.prune" :disabled="isRunning" />
        <span class="hint">遍历时按下方阈值清理低价值记忆</span>
      </el-form-item>
      <el-form-item v-if="reindexForm.prune" label="优先级阈值">
        <el-input-number
          v-model="reindexForm.priority_threshold"
          :min="0.01"
          :max="0.5"
          :step="0.01"
          :precision="2"
          :disabled="isRunning"
        />
        <span class="hint">theoretical_priority &lt; 阈值 且非 PERMANENT 才会被清理</span>
      </el-form-item>
      <el-form-item>
        <el-button
          type="primary"
          :loading="reindexStarting"
          :disabled="isRunning"
          @click="$emit('start-reindex')"
        >
          {{ isRunning ? '进行中…' : '启动' }}
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style lang="scss" scoped>
.sec-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sec-title {
  font-weight: 600;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: $space-2;
}

.err {
  color: var(--el-color-danger);
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
