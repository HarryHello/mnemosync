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
  margin-bottom: $space-5;
}

.page-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-4;

  :deep(.page-title) {
    margin-bottom: 0;
  }
}

.page-header-actions {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-shrink: 0;
}

:deep(.page-subtitle) {
  margin-top: $space-1;
  margin-bottom: 0;
}
</style>
