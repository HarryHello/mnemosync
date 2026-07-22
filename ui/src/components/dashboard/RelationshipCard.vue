<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Relationship } from '@/types/api'

const props = defineProps<{
  relationship?: Relationship | null
  loading?: boolean
}>()

const router = useRouter()

function goToRelationships() {
  router.push('/relationships')
}

// 根据值返回进度条颜色
function getProgressType(value: number): '' | 'success' | 'warning' | 'exception' {
  if (value >= 0.65) return 'success'
  if (value >= 0.4) return ''
  if (value >= 0.2) return 'warning'
  return 'exception'
}

// 格式化显示值 (三位小数)
const formatValue = (v: number) => v.toFixed(3)
</script>

<template>
  <el-card v-loading="props.loading" class="relationship-card">
    <div class="card-header">
      <span class="card-title">关系</span>
      <el-button link type="primary" size="small" @click="goToRelationships">
        查看详情
      </el-button>
    </div>

    <div class="metrics">
      <div class="metric-row">
        <div class="metric-label">亲密度</div>
        <div class="metric-bar">
          <el-progress
            :percentage="(relationship?.intimacy ?? 0) * 100"
            :stroke-width="8"
            :status="getProgressType(relationship?.intimacy ?? 0)"
            :show-text="false"
          />
        </div>
        <div class="metric-value mono">{{ formatValue(relationship?.intimacy ?? 0) }}</div>
      </div>

      <div class="metric-row">
        <div class="metric-label">信任度</div>
        <div class="metric-bar">
          <el-progress
            :percentage="(relationship?.trust ?? 0) * 100"
            :stroke-width="8"
            :status="getProgressType(relationship?.trust ?? 0)"
            :show-text="false"
          />
        </div>
        <div class="metric-value mono">{{ formatValue(relationship?.trust ?? 0) }}</div>
      </div>
    </div>
  </el-card>
</template>

<style lang="scss" scoped>
.relationship-card {
  transition: transform 0.15s ease;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: $space-3;
  padding-bottom: $space-2;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.card-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--el-text-color-primary);
}

.metrics {
  display: flex;
  flex-direction: column;
  gap: $space-4;
}

.metric-row {
  display: flex;
  align-items: center;
  gap: $space-3;
}

.metric-label {
  width: 60px;
  flex-shrink: 0;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.metric-bar {
  flex: 1;
  min-width: 0;
}

.metric-value {
  width: 55px;
  flex-shrink: 0;
  text-align: right;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>
