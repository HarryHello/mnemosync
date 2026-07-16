<script setup lang="ts">
const title = '请求日志'
</script>

<template>
  <div class="page-container">
    <h2 class="page-title">{{ title }}</h2>
    <p class="page-subtitle">
      记录所有经过网关的 HTTP 请求与响应，用于排查上游/下游通信问题。
    </p>
    <el-empty description="敬请期待" />
  </div>
</template>

<style lang="scss" scoped>
:deep(.el-empty) {
  padding: $space-8 0;
}
</style>
