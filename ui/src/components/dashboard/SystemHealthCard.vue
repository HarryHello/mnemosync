<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { BackendStatusResponse, HealthResponse } from '@/types/api'
import { getBackendStatus, restartBackend, startBackend, stopBackend } from '@/api/client'

const props = defineProps<{
  health: HealthResponse | null
  error: string | null
}>()

const backendStatus = ref<BackendStatusResponse | null>(null)
const backendError = ref<string | null>(null)
const backendLoading = ref(false)
const actionLoading = ref(false)

const running = computed(() => backendStatus.value?.running ?? false)

async function refreshBackendStatus() {
  backendLoading.value = true
  backendError.value = null
  try {
    backendStatus.value = await getBackendStatus()
  } catch (err) {
    backendError.value = err instanceof Error ? err.message : String(err)
    backendStatus.value = null
  } finally {
    backendLoading.value = false
  }
}

async function runAction(action: () => Promise<{ message: string }>) {
  actionLoading.value = true
  try {
    const res = await action()
    ElMessage.success(res.message || '操作成功')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    actionLoading.value = false
    await refreshBackendStatus()
  }
}

function handleStart() {
  return runAction(() => startBackend())
}

function handleStop() {
  return runAction(() => stopBackend())
}

function handleRestart() {
  return runAction(() => restartBackend())
}

onMounted(refreshBackendStatus)
</script>

<template>
  <el-card class="health-card">
    <template #header>
      <div class="card-header">
        <span>系统健康</span>
        <div class="header-right">
          <el-tag v-if="backendLoading" size="small" type="info">检测中</el-tag>
          <el-tag v-else-if="running" type="success" size="small">运行中</el-tag>
          <el-tag v-else-if="backendError" type="danger" size="small">未知</el-tag>
          <el-tag v-else type="danger" size="small">已停止</el-tag>
          <el-button
            v-if="!running"
            :loading="actionLoading"
            type="primary"
            size="small"
            @click="handleStart"
          >启动</el-button>
          <template v-else>
            <el-button
              :loading="actionLoading"
              type="danger"
              size="small"
              @click="handleStop"
            >停止</el-button>
            <el-button
              :loading="actionLoading"
              size="small"
              @click="handleRestart"
            >重启</el-button>
          </template>
        </div>
      </div>
    </template>

    <el-descriptions v-if="props.health" :column="2" border>
      <el-descriptions-item label="状态">{{ props.health.status }}</el-descriptions-item>
      <el-descriptions-item label="版本">{{ props.health.version }}</el-descriptions-item>
      <el-descriptions-item label="检查时间" :span="2">
        <span class="mono">{{ new Date(props.health.timestamp).toLocaleString() }}</span>
      </el-descriptions-item>
    </el-descriptions>

    <el-alert
      v-else-if="props.error"
      type="error"
      :closable="false"
      :title="'健康检查失败: ' + props.error"
    />

    <el-alert
      v-else-if="!props.health && !running"
      type="warning"
      :closable="false"
      title="后端进程未在运行, 无法获取健康数据"
    />

    <el-skeleton v-else :rows="2" animated />
  </el-card>
</template>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
  justify-content: space-between;
  width: 100%;
  font-weight: 700;
}

.header-right {
  display: flex;
  align-items: center;
  gap: $space-2;
}
</style>