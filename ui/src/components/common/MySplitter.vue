<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 面板排列方向 */
    layout?: 'horizontal' | 'vertical'
    /** 分隔条厚度 (CSS 长度值) */
    gapSize?: string
    /** 拖动时是否延迟更新面板大小 */
    lazy?: boolean
  }>(),
  {
    layout: 'horizontal',
    gapSize: '8px',
    lazy: false,
  },
)

const emit = defineEmits<{
  resizeStart: [index: number, sizes: number[]]
  resize: [index: number, sizes: number[]]
  resizeEnd: [index: number, sizes: number[]]
  collapse: [index: number, type: 'start' | 'end', sizes: number[]]
}>()

const splitterStyle = computed(() => ({ '--splitter-gap-size': props.gapSize }))
</script>

<template>
  <div class="splitter-wrapper" :class="`is-${layout}`" :style="splitterStyle">
    <el-splitter
      :layout="layout"
      :lazy="lazy"
      class="splitter"
      @resize-start="(index: number, sizes: number[]) => emit('resizeStart', index, sizes)"
      @resize="(index: number, sizes: number[]) => emit('resize', index, sizes)"
      @resize-end="(index: number, sizes: number[]) => emit('resizeEnd', index, sizes)"
      @collapse="
        (index: number, type: 'start' | 'end', sizes: number[]) =>
          emit('collapse', index, type, sizes)
      "
    >
      <slot />
    </el-splitter>
  </div>
</template>

<style lang="scss" scoped>
.splitter-wrapper {
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

.splitter {
  height: 100%;
}

.is-horizontal :deep(.el-splitter-bar) {
  width: var(--splitter-gap-size) !important;
}

.is-vertical :deep(.el-splitter-bar) {
  height: var(--splitter-gap-size) !important;
}

:deep(.el-splitter-bar__dragger) {
  color: var(--el-border-color-light);

  &::before {
    display: none;
  }
}
</style>
