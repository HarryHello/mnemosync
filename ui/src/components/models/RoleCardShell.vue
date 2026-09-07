<script setup lang="ts">
defineProps<{
  title: string
  desc: string
  badgeText?: string
  badgeTone?: 'info' | 'warn'
}>()
</script>

<template>
  <el-card class="role-card">
    <template #header>
      <div class="role-head">
        <div class="role-title">
          <span>{{ title }}</span>
          <span
            v-if="badgeText"
            class="role-badge"
            :class="{ 'role-badge-warn': badgeTone === 'warn' }"
          >
            {{ badgeText }}
          </span>
        </div>
        <div class="role-head-actions">
          <slot name="actions" />
        </div>
      </div>
      <div class="role-desc">{{ desc }}</div>
    </template>
    <slot />
  </el-card>
</template>

<style lang="scss" scoped>
.role-head {
  display: flex;
  gap: $space-2;
  align-items: center;
  justify-content: space-between;
}

.role-head-actions {
  display: flex;
  gap: $space-2;
}

.role-title {
  display: flex;
  gap: $space-2;
  align-items: center;
  font-weight: 600;
}

.role-badge {
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
  border-radius: $radius-sm;
}

.role-badge-warn {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.role-desc {
  margin-top: $space-1;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
