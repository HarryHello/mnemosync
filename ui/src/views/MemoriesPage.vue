<script setup lang="ts">
import { ref } from 'vue'
import MemoriesTab from '@/components/memories/MemoriesTab.vue'
import ContextTab from '@/components/memories/ContextTab.vue'

const activeTab = ref<'memories' | 'context'>('memories')
</script>

<template>
  <div class="memories-page">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="长期记忆" name="memories">
        <MemoriesTab :active="activeTab === 'memories'" />
      </el-tab-pane>
      <el-tab-pane label="短期记忆（上下文）" name="context">
        <ContextTab :active="activeTab === 'context'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style lang="scss" scoped>
.memories-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: $space-5;
  max-width: $content-max-width;
  margin: 0 auto;
  box-sizing: border-box;

  :deep(.el-tabs) {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  :deep(.el-tabs__content) {
    flex: 1;
    overflow: hidden;
  }

  :deep(.el-tab-pane) {
    height: 100%;
  }
}
</style>
