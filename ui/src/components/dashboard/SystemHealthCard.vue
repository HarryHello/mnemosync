<script setup lang="ts">
import type { HealthResponse } from '@/types/api'

const props = defineProps<{
  health: HealthResponse | null
  error: string | null
}>()
</script>

<template>
  <el-card class="health-card">
    <template #header>
      <div class="card-header">
        <span>系统健康</span>
        <el-tag v-if="props.health" type="success" size="small">运行中</el-tag>
        <el-tag v-else-if="props.error" type="danger" size="small">离线</el-tag>
        <el-tag v-else size="small">加载中</el-tag>
      </div>
    </template>
    <el-descriptions v-if="props.health" :column="2" border>
      <el-descriptions-item label="状态">{{ props.health.status }}</el-descriptions-item>
      <el-descriptions-item label="版本">{{ props.health.version }}</el-descriptions-item>
      <el-descriptions-item label="检查时间" :span="2">
        <span class="mono">{{ new Date(props.health.timestamp).toLocaleString() }}</span>
      </el-descriptions-item>
    </el-descriptions>
    <el-alert
      v-else-if="props.error"
      type="error"
      :closable="false"
      :title="'健康检查失败: ' + props.error"
    />
    <el-skeleton v-else :rows="2" animated />
  </el-card>
</template>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
  justify-content: space-between;
  width: 100%;
  font-weight: 700;
}
</style>
