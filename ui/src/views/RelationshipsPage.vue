<script setup lang="ts">
const title = '关系状态'
</script>

<template>
  <div class="page-container">
    <h2 class="page-title">{{ title }}</h2>
    <p class="page-subtitle">
      查看 Mnemosync 与当前用户之间的亲密度 / 信任度 / 关系类型等状态。
    </p>
    <el-empty description="敬请期待" />
  </div>
</template>

<style lang="scss" scoped>
:deep(.el-empty) {
  padding: $space-8 0;
}
</style>
