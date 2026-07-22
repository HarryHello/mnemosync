<script setup lang="ts">
import { ref } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'
import MemoriesTab from '@/components/memories/MemoriesTab.vue'
import ContextTab from '@/components/memories/ContextTab.vue'
import MaintenanceTab from '@/components/memories/MaintenanceTab.vue'

const activeTab = ref<'memories' | 'context' | 'maintenance'>('memories')
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane label="长期记忆" name="memories">
        <MemoriesTab :active="activeTab === 'memories'" />
      </el-tab-pane>
      <el-tab-pane label="短期记忆（上下文）" name="context">
        <ContextTab :active="activeTab === 'context'" />
      </el-tab-pane>
      <el-tab-pane label="记忆维护" name="maintenance">
        <MaintenanceTab :active="activeTab === 'maintenance'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
