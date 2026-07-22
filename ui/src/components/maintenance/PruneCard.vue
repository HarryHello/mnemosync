<script setup lang="ts">
import type { PruneResponse } from '@/types/api'

const props = defineProps<{
  pruneForm: {
    priority_threshold: number
  }
  prunePreview: PruneResponse | null
  previewLoading: boolean
  pruneRunning: boolean
  isRunning: boolean
}>()

const emit = defineEmits<{
  'preview-prune': []
  'run-prune': []
}>()
</script>

<template>
  <el-card shadow="hover" class="section equal-card">
    <template #header>
      <div class="sec-head">
        <span class="sec-title">清理低价值记忆 (Prune)</span>
      </div>
    </template>

    <el-form label-width="120px" size="default">
      <el-form-item label="优先级阈值">
        <el-input-number
          v-model="pruneForm.priority_threshold"
          :min="0.01"
          :max="0.5"
          :step="0.01"
          :precision="2"
        />
        <span class="hint">同 Reindex 阈值语义</span>
      </el-form-item>
      <el-form-item>
        <el-button
          :loading="previewLoading"
          :disabled="isRunning"
          @click="$emit('preview-prune')"
        >
          预览
        </el-button>
        <el-button
          type="danger"
          :loading="pruneRunning"
          :disabled="isRunning || !prunePreview || prunePreview.would_delete === 0"
          @click="$emit('run-prune')"
        >
          执行清理
        </el-button>
      </el-form-item>
    </el-form>

    <el-divider v-if="prunePreview" />

    <el-descriptions v-if="prunePreview" :column="1" size="small" border>
      <el-descriptions-item label="总数 (预览前)">
        {{ prunePreview.total_before }}
      </el-descriptions-item>
      <el-descriptions-item label="预计删除">
        {{ prunePreview.would_delete }}
      </el-descriptions-item>
      <el-descriptions-item label="实际已删">
        {{ prunePreview.deleted }}
      </el-descriptions-item>
      <el-descriptions-item label="分类">
        <div class="chip-row">
          <span class="meta-chip">forgotten {{ prunePreview.breakdown.forgotten }}</span>
          <span class="meta-chip">expired {{ prunePreview.breakdown.expired }}</span>
          <span class="meta-chip">low_priority {{ prunePreview.breakdown.low_priority }}</span>
        </div>
      </el-descriptions-item>
    </el-descriptions>
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

.chip-row {
  display: flex;
  gap: $space-2;
  flex-wrap: wrap;
}

.meta-chip {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: $radius-sm;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}
</style>
