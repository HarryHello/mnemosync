<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { BackendStatusResponse, HealthResponse } from '@/types/api'
import { getBackendStatus, restartBackend, startBackend, stopBackend } from '@/api/client'

const props = defineProps<{
  health: HealthResponse | null
  error: string | null
}>()

const emit = defineEmits<{
  'backend-started': []
}>()

const backendStatus = ref<BackendStatusResponse | null>(null)
const backendError = ref<string | null>(null)
const backendLoading = ref(false)
const actionLoading = ref(false)
// 是否为前后端分离模式 (面板进程提供后端管理路由)
const isPanelMode = ref(true)

const running = computed(() => {
  if (isPanelMode.value) {
    return backendStatus.value?.running ?? false
  }
  // 单进程模式: 用 props.health 判断 (来自 /health 端点)
  return props.health?.status === 'ok'
})

async function refreshBackendStatus() {
  backendLoading.value = true
  backendError.value = null
  try {
    backendStatus.value = await getBackendStatus()
    isPanelMode.value = true
  } catch (err) {
    // 路由不存在 (404/405) = 单进程模式, 无后端管理路由
    if (err instanceof Error && /404|405|Not Found/.test(err.message)) {
      isPanelMode.value = false
    } else {
      backendError.value = err instanceof Error ? err.message : String(err)
    }
    backendStatus.value = null
  } finally {
    backendLoading.value = false
  }
}

async function runAction(action: () => Promise<{ message: string }>, pollUntilRunning = false) {
  actionLoading.value = true
  try {
    const res = await action()
    ElMessage.success(res.message || '操作成功')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    actionLoading.value = false
    if (pollUntilRunning) {
      // 轮询等待后端就绪 (最多 10 秒)
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 1000))
        await refreshBackendStatus()
        if (running.value) break
      }
      if (running.value) emit('backend-started')
    } else {
      await refreshBackendStatus()
    }
  }
}

function handleStart() {
  return runAction(() => startBackend(), true)
}

function handleStop() {
  return runAction(() => stopBackend())
}

function handleRestart() {
  return runAction(() => restartBackend(), true)
}

onMounted(refreshBackendStatus)
</script>

<template>
  <el-card class="health-card">
    <template #header>
      <div class="card-header">
        <span>
          系统健康 &ThickSpace;
          <el-tag v-if="backendLoading" size="small" type="info">检测中</el-tag>
          <el-tag v-else-if="running" type="success" size="small">运行中</el-tag>
          <el-tag v-else-if="backendError" type="danger" size="small">未知</el-tag>
          <el-tag v-else type="danger" size="small">已停止</el-tag>
        </span>
        <div class="header-right" v-if="isPanelMode">
          <el-button
            v-if="!running"
            :loading="actionLoading"
            type="primary"
            size="small"
            @click="handleStart"
            round
          >启动</el-button>
          <template v-else>
            <el-button
              :loading="actionLoading"
              type="danger"
              size="small"
              @click="handleStop"
              round
            >停止</el-button>
            <el-button
              :loading="actionLoading"
              size="small"
              @click="handleRestart"
              round
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
      :title="isPanelMode ? '后端进程未在运行, 无法获取健康数据' : '无法获取健康数据'"
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