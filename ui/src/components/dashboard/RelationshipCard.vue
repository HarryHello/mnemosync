<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Relationship } from '@/types/api'

const props = defineProps<{
  relationships?: Relationship[]
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

const topUsers = computed(() => (props.relationships ?? []).slice(0, 5))

function fmtDate(s: string | null): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('zh-CN')
}
</script>

<template>
  <el-card v-loading="props.loading" class="relationship-card">
    <div class="card-header">
      <span class="card-title">关系</span>
      <el-button link type="primary" size="small" @click="goToRelationships">
        查看详情
      </el-button>
    </div>

    <el-empty
      v-if="!loading && topUsers.length === 0"
      description="尚无关系记录"
      :image-size="50"
    />

    <div v-else class="user-list">
      <div
        v-for="rel in topUsers"
        :key="rel.user_id"
        class="user-row"
      >
        <div class="user-id mono">{{ rel.user_id }}</div>
        <div class="user-metrics">
          <div class="metric-line">
            <span class="metric-label">亲密度</span>
            <el-progress
              :percentage="rel.intimacy * 100"
              :stroke-width="6"
              :status="getProgressType(rel.intimacy)"
              :show-text="false"
            />
            <span class="metric-value">{{ formatValue(rel.intimacy) }}</span>
          </div>
          <div class="metric-line">
            <span class="metric-label">信任度</span>
            <el-progress
              :percentage="rel.trust * 100"
              :stroke-width="6"
              :status="getProgressType(rel.trust)"
              :show-text="false"
            />
            <span class="metric-value">{{ formatValue(rel.trust) }}</span>
          </div>
        </div>
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

.user-list {
  display: flex;
  flex-direction: column;
  gap: $space-3;
}

.user-row {
  display: flex;
  flex-direction: column;
  gap: $space-1;
  padding: $space-2;
  border-radius: $radius-md;
  background: var(--el-fill-color-lighter);
}

.user-id {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

.user-metrics {
  display: flex;
  flex-direction: column;
  gap: $space-1;
}

.metric-line {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.metric-label {
  width: 48px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.metric-value {
  width: 50px;
  flex-shrink: 0;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>
