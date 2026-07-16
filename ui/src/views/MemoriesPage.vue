<script setup lang="ts">
const title = '记忆管理'
</script>

<template>
  <div class="page-container">
    <h2 class="page-title">{{ title }}</h2>
    <p class="page-subtitle">查看与管理 Mnemosync 存储的长期/短期记忆条目。</p>
    <el-empty description="敬请期待" />
  </div>
</template>

<style lang="scss" scoped>
:deep(.el-empty) {
  padding: $space-8 0;
}
</style>
