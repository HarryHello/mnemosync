<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Relationship } from '@/types/api'
import { formatDateOnly } from '@/utils/format'

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

function identityName(rel: Relationship): string {
  const account = rel.identity?.accounts[0]
  return rel.identity?.name || account?.display_name || account?.external_key || rel.user_id
}

function identityDetail(rel: Relationship): string {
  const accounts = rel.identity?.accounts ?? []
  if (accounts.length === 0) return rel.user_id
  return accounts
    .map((account) => `${account.frontend} · ${account.external_key}`)
    .join(' / ')
}

const topUsers = computed(() => (props.relationships ?? []).slice(0, 5))
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
        <div class="user-identity">
          <span class="user-name">{{ identityName(rel) }}</span>
          <span class="user-source mono">{{ identityDetail(rel) }}</span>
        </div>
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

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: $space-4;
  padding-bottom: $space-3;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.card-title {
  font-weight: 700;
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
  gap: $space-2;
  padding: $space-3;
  border-radius: $radius-md;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
}

.user-identity {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.user-name {
  overflow: hidden;
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-source {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.metric-line :deep(.el-progress) {
  flex: 1;
}

.metric-line :deep(.el-progress-bar__outer) {
  background: var(--el-fill-color-dark);
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
