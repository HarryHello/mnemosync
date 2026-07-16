<script setup lang="ts">
// 空白布局: 只承载 <router-view />, 用于登录等需要独立背景的页面.
</script>

<template>
  <div class="blank-layout">
    <router-view />
  </div>
</template>

<style lang="scss" scoped>
.blank-layout {
  min-height: 100vh;
  width: 100%;
  background: linear-gradient(135deg, #f5f7fb 0%, #eef2f9 100%);

  :root.dark & {
    background: linear-gradient(135deg, #1a1a1e 0%, #0f0f12 100%);
  }
}
</style>
