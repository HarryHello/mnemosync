<script setup lang="ts">
import { ref } from 'vue'
import MemoriesTab from '@/components/memories/MemoriesTab.vue'
import ContextTab from '@/components/memories/ContextTab.vue'

const activeTab = ref<'memories' | 'context'>('memories')
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="memories-tabs">
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
.memories-tabs {
  :deep(.el-tabs__content) {
    padding: 0 $space-2 $space-2;
  }
}
</style>
