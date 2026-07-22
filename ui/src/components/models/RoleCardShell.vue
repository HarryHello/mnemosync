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
  align-items: center;
  justify-content: space-between;
  gap: $space-2;
}

.role-head-actions {
  display: flex;
  gap: $space-2;
}

.role-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: $space-2;
}

.role-badge {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  padding: 2px 8px;
  border-radius: $radius-sm;
  background: var(--el-fill-color);
}

.role-badge-warn {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.role-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: $space-1;
}
</style>
