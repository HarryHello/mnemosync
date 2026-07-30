<script setup lang="ts">
import type { PersonaResetResponse } from '@/types/api'

const props = defineProps<{
  resetPreview: PersonaResetResponse | null
  resetPreviewLoading: boolean
  resetRunning: boolean
  isRunning: boolean
}>()

const emit = defineEmits<{
  'preview-reset': []
  'run-reset': []
}>()
</script>

<template>
  <el-card class="section danger-section">
    <template #header>
      <div class="sec-head">
        <span class="sec-title">重置人格状态 (Persona Reset)</span>
        <el-tag type="danger" size="small">危险</el-tag>
      </div>
    </template>

    <p class="reset-desc">
      清空所有长期记忆 (含 PERMANENT) / 关系 (亲密度 · 信任度) / 短期对话流水 / 向量库,
      回到"新装"语义。<b>不会</b>动 API Key / 服务商 / 提示词 / 模型绑定 / 管理员账户。
      删除后不可恢复; 与 Reindex 互斥 (进行中会 409)。
    </p>

    <el-form label-width="120px" size="default">
      <el-form-item>
        <el-button
          :loading="resetPreviewLoading"
          :disabled="isRunning || resetRunning"
          @click="$emit('preview-reset')"
        >
          预览
        </el-button>
        <el-button
          type="danger"
          :loading="resetRunning"
          :disabled="isRunning || !resetPreview"
          @click="$emit('run-reset')"
        >
          执行重置
        </el-button>
      </el-form-item>
    </el-form>

    <el-divider v-if="resetPreview" />

    <el-descriptions v-if="resetPreview" :column="1" size="small" border>
      <el-descriptions-item label="将清 长期记忆">
        {{ resetPreview.deleted_memories }} 条 (含 PERMANENT)
      </el-descriptions-item>
      <el-descriptions-item label="将清 关系">
        {{ resetPreview.deleted_relationships }} 条
      </el-descriptions-item>
      <el-descriptions-item label="将清 短期对话">
        {{ resetPreview.deleted_conversation_turns }} 条
      </el-descriptions-item>
      <el-descriptions-item label="向量库">
        {{ resetPreview.vector_reset ? '已重建 collection' : '未触发 (dry-run)' }}
      </el-descriptions-item>
      <el-descriptions-item v-if="resetPreview.errors.length > 0" label="错误">
        <div v-for="err in resetPreview.errors" :key="err" class="err">
          {{ err }}
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

.danger-section {
  border: 1px solid var(--el-color-danger-light-7);
}

.reset-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin: 0 0 $space-3 0;
  line-height: 1.6;
}

.err {
  color: var(--el-color-danger);
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
