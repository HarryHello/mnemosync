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
  <el-card v-loading="props.loading" class="stat-card">
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
  border: 1px solid var(--el-border-color);
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
  border: 1px solid transparent;

  &.prompt {
    color: white;
    background: #4285f4;
  }

  &.key {
    color: white;
    background: #34a853;
  }

  &.memory {
    color: white;
    background: #ef820e;
  }

  &.log {
    color: white;
    background: #64748b;
  }
}

.stat-body { min-width: 0; }

.stat-label {
  font-size: 12px;
  letter-spacing: 0.02em;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  margin-top: 4px;
  letter-spacing: -0.03em;
}

.stat-total {
  font-size: 14px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.stat-footer {
  margin-top: $space-3;
  padding-top: $space-3;
  border-top: 1px solid var(--el-border-color-lighter);
  text-align: right;
}
</style>
