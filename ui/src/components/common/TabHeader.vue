<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="tab-head">
    <div>
      <h3 class="tab-title">{{ title }}</h3>
      <p v-if="$slots.subtitle || subtitle" class="tab-subtitle">
        <slot name="subtitle">{{ subtitle }}</slot>
      </p>
    </div>
    <div v-if="$slots.actions" class="tab-head-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  gap: $space-4;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: $space-4;
}

.tab-title {
  margin: 0 0 $space-1;
  font-size: 18px;
  font-weight: 600;
}

.tab-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.tab-head-actions {
  display: flex;
  flex-shrink: 0;
  gap: $space-2;
  align-items: center;
}
</style>
