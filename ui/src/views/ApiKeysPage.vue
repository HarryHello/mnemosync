<script setup lang="ts">
const title = 'API Key'
</script>

<template>
  <div class="page-container">
    <h2 class="page-title">{{ title }}</h2>
    <p class="page-subtitle">用于第三方客户端访问 Mnemosync 的 API 密钥管理。</p>
    <el-empty description="敬请期待" />
  </div>
</template>

<style lang="scss" scoped>
:deep(.el-empty) {
  padding: $space-8 0;
}
</style>
