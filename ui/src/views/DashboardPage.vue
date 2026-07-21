<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getDashboardStats } from '@/api/client'
import type { HealthResponse } from '@/types/api'
import { useAuthStore } from '@/stores/auth'
import StatGrid from '@/components/dashboard/StatGrid.vue'
import SystemHealthCard from '@/components/dashboard/SystemHealthCard.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const authStore = useAuthStore()

const health = ref<HealthResponse | null>(null)
const healthErr = ref<string | null>(null)

const apiKeyCount = ref<number | null>(null)
const memoryCount = ref<number | null>(null)
const logCount = ref<number | null>(null)
const promptOverriddenCount = ref<number | null>(null)
const promptTotalCount = ref<number | null>(null)
const loading = ref(true)

const username = computed(() => authStore.user?.username ?? '未知用户')

async function refresh() {
  loading.value = true
  try {
    const stats = await getDashboardStats()
    health.value = stats.health
    healthErr.value = null
    apiKeyCount.value = stats.api_keys
    memoryCount.value = stats.memories
    logCount.value = stats.logs
    promptTotalCount.value = stats.prompts_total
    promptOverriddenCount.value = stats.prompts_overridden
  } catch (err) {
    healthErr.value = err instanceof Error ? err.message : String(err)
    apiKeyCount.value = null
    memoryCount.value = null
    logCount.value = null
    promptTotalCount.value = null
    promptOverriddenCount.value = null
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <PageHeader :title="`你好, ${username}`">
      <template #subtitle>
        Mnemosync 管理面板<span v-if="health"> · v{{ health.version }}</span>
      </template>
      <template #actions>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <StatGrid
      class="stat-grid-block"
      :loading="loading"
      :api-key-count="apiKeyCount"
      :memory-count="memoryCount"
      :log-count="logCount"
      :prompt-overridden-count="promptOverriddenCount"
      :prompt-total-count="promptTotalCount"
    />

    <SystemHealthCard :health="health" :error="healthErr" />
  </div>
</template>

<style lang="scss" scoped>
.stat-grid-block {
  margin-bottom: $space-5;
}
</style>
