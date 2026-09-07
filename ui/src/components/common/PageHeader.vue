<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="page-header">
    <div class="page-header-row">
      <h2 class="page-title">{{ title }}</h2>
      <div v-if="$slots.actions" class="page-header-actions">
        <slot name="actions" />
      </div>
    </div>
    <p v-if="$slots.subtitle || subtitle" class="page-subtitle">
      <slot name="subtitle">{{ subtitle }}</slot>
    </p>
  </div>
</template>

<style lang="scss" scoped>
.page-header {
  padding-bottom: $space-4;
  margin-bottom: $space-5;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.page-header-row {
  display: flex;
  gap: $space-4;
  align-items: center;
  justify-content: space-between;

  :deep(.page-title) {
    margin-bottom: 0;
  }
}

.page-header-actions {
  display: flex;
  flex-shrink: 0;
  gap: $space-2;
  align-items: center;
}

:deep(.page-subtitle) {
  max-width: 720px;
  margin-top: 6px;
  margin-bottom: 0;
}
</style>
