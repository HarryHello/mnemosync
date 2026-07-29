<script setup lang="ts">
import { ref } from 'vue'
import UpstreamTab from '@/components/models/UpstreamTab.vue'
import ModelTab from '@/components/models/ModelTab.vue'

const activeTab = ref<'upstream' | 'models'>('upstream')
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane label="上游 API" name="upstream">
        <UpstreamTab />
      </el-tab-pane>
      <el-tab-pane label="模型管理" name="models">
        <ModelTab />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
