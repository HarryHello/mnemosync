<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

type Tone = 'prompt' | 'key' | 'memory' | 'log'

const props = defineProps<{
  label: string
  value: string
  total?: string
  tone: Tone
  actionText: string
  to: string
  loading?: boolean
}>()

const router = useRouter()

function go() {
  router.push(props.to)
}
</script>

<template>
  <el-card v-loading="props.loading" shadow="hover" class="stat-card">
    <div class="stat">
      <div :class="['stat-icon', tone]">
        <slot name="icon" />
      </div>
      <div class="stat-body">
        <div class="stat-label">{{ label }}</div>
        <div class="stat-value">
          {{ value }}
          <span v-if="total !== undefined" class="stat-total"> / {{ total }}</span>
        </div>
      </div>
    </div>
    <div class="stat-footer">
      <el-button link type="primary" @click="go">
        {{ actionText }}
        <el-icon><ArrowRight /></el-icon>
      </el-button>
    </div>
  </el-card>
</template>

<style lang="scss" scoped>
.stat-card {
  transition: transform 0.15s ease;

  &:hover {
    transform: translateY(-2px);
  }
}

.stat {
  display: flex;
  align-items: center;
  gap: $space-3;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: $radius-md;
  @include flex-center;
  color: #fff;

  &.prompt { background: linear-gradient(135deg, #4f8cff, #6ba0ff); }
  &.key    { background: linear-gradient(135deg, #67c23a, #85ce61); }
  &.memory { background: linear-gradient(135deg, #e6a23c, #ebb563); }
  &.log    { background: linear-gradient(135deg, #909399, #a6a9ad); }
}

.stat-body { min-width: 0; }

.stat-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-top: 2px;
}

.stat-total {
  font-size: 14px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.stat-footer {
  margin-top: $space-3;
  padding-top: $space-2;
  border-top: 1px dashed var(--el-border-color-lighter);
  text-align: right;
}
</style>
