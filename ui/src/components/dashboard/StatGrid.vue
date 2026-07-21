<script setup lang="ts">
import { EditPen, Key, Cpu, Document } from '@element-plus/icons-vue'
import StatCard from './StatCard.vue'

const props = defineProps<{
  loading?: boolean
  apiKeyCount: number | null
  memoryCount: number | null
  logCount: number | null
  promptOverriddenCount: number | null
  promptTotalCount: number | null
}>()

function fmt(n: number | null): string {
  return n === null ? '—' : String(n)
}
</script>

<template>
  <div class="stat-grid">
    <StatCard
      tone="prompt"
      label="提示词覆盖"
      :value="fmt(props.promptOverriddenCount)"
      :total="fmt(props.promptTotalCount)"
      action-text="管理提示词"
      to="/prompts"
      :loading="props.loading"
    >
      <template #icon>
        <el-icon :size="24"><EditPen /></el-icon>
      </template>
    </StatCard>

    <StatCard
      tone="key"
      label="API Key"
      :value="fmt(props.apiKeyCount)"
      action-text="管理 Key"
      to="/api-keys"
      :loading="props.loading"
    >
      <template #icon>
        <el-icon :size="24"><Key /></el-icon>
      </template>
    </StatCard>

    <StatCard
      tone="memory"
      label="记忆条目"
      :value="fmt(props.memoryCount)"
      action-text="查看记忆"
      to="/memories"
      :loading="props.loading"
    >
      <template #icon>
        <el-icon :size="24"><Cpu /></el-icon>
      </template>
    </StatCard>

    <StatCard
      tone="log"
      label="请求日志"
      :value="fmt(props.logCount)"
      action-text="查看日志"
      to="/logs"
      :loading="props.loading"
    >
      <template #icon>
        <el-icon :size="24"><Document /></el-icon>
      </template>
    </StatCard>
  </div>
</template>

<style lang="scss" scoped>
.stat-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(md) {
    grid-template-columns: repeat(2, 1fr);
  }

  @include respond-to(xl) {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
