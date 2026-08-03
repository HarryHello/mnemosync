<script setup lang="ts">
import { ref } from 'vue'
import UpstreamTab from '@/components/models/UpstreamTab.vue'
import ModelTab from '@/components/models/ModelTab.vue'

const activeTab = ref<'upstream' | 'models'>('upstream')
const upstreamRef = ref<InstanceType<typeof UpstreamTab> | null>(null)
const modelRef = ref<InstanceType<typeof ModelTab> | null>(null)

function onTabChange(name: string) {
  if (name === 'models') {
    modelRef.value?.refresh()
  } else if (name === 'upstream') {
    upstreamRef.value?.refresh()
  }
}
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="page-tabs" @tab-change="onTabChange">
      <el-tab-pane label="上游 API" name="upstream">
        <UpstreamTab ref="upstreamRef" />
      </el-tab-pane>
      <el-tab-pane label="模型管理" name="models">
        <ModelTab ref="modelRef" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
