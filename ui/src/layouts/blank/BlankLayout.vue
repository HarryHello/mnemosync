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
  background:
    radial-gradient(circle at top right, rgba(66, 133, 244, 0.14), transparent 28%),
    linear-gradient(180deg, #f8fbff 0%, #f2f6fb 100%);

  :root.dark & {
    background:
      radial-gradient(circle at top right, rgba(252, 44, 80, 0.12), transparent 24%),
      linear-gradient(180deg, #171717 0%, #101010 100%);
  }
}
</style>
