<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getDashboardStats } from '@/api/client'
import type { HealthResponse } from '@/types/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
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

function fmt(n: number | null): string {
  return n === null ? '—' : String(n)
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="hero">
      <div>
        <h2 class="hello">你好, {{ username }}</h2>
        <p class="page-subtitle">
          Mnemosync 管理面板
          <span v-if="health"> · v{{ health.version }}</span>
        </p>
      </div>
      <div class="hero-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <div class="cards">
      <el-card v-loading="loading" shadow="hover" class="stat-card">
        <div class="stat">
          <div class="stat-icon prompt">
            <el-icon :size="24"><EditPen /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">提示词覆盖</div>
            <div class="stat-value">
              {{ fmt(promptOverriddenCount) }}
              <span class="stat-total"> / {{ fmt(promptTotalCount) }}</span>
            </div>
          </div>
        </div>
        <div class="stat-footer">
          <el-button link type="primary" @click="router.push('/prompts')">
            管理提示词
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </el-card>

      <el-card v-loading="loading" shadow="hover" class="stat-card">
        <div class="stat">
          <div class="stat-icon key">
            <el-icon :size="24"><Key /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">API Key</div>
            <div class="stat-value">{{ fmt(apiKeyCount) }}</div>
          </div>
        </div>
        <div class="stat-footer">
          <el-button link type="primary" @click="router.push('/api-keys')">
            管理 Key
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </el-card>

      <el-card v-loading="loading" shadow="hover" class="stat-card">
        <div class="stat">
          <div class="stat-icon memory">
            <el-icon :size="24"><Cpu /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">记忆条目</div>
            <div class="stat-value">{{ fmt(memoryCount) }}</div>
          </div>
        </div>
        <div class="stat-footer">
          <el-button link type="primary" @click="router.push('/memories')">
            查看记忆
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </el-card>

      <el-card v-loading="loading" shadow="hover" class="stat-card">
        <div class="stat">
          <div class="stat-icon log">
            <el-icon :size="24"><Document /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">请求日志</div>
            <div class="stat-value">{{ fmt(logCount) }}</div>
          </div>
        </div>
        <div class="stat-footer">
          <el-button link type="primary" @click="router.push('/logs')">
            查看日志
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </el-card>
    </div>

    <el-card shadow="never" class="health-card">
      <template #header>
        <div class="card-header">
          <span>系统健康</span>
          <el-tag v-if="health" type="success" size="small">运行中</el-tag>
          <el-tag v-else-if="healthErr" type="danger" size="small">离线</el-tag>
          <el-tag v-else size="small">加载中</el-tag>
        </div>
      </template>
      <el-descriptions v-if="health" :column="2" border>
        <el-descriptions-item label="状态">{{ health.status }}</el-descriptions-item>
        <el-descriptions-item label="版本">{{ health.version }}</el-descriptions-item>
        <el-descriptions-item label="检查时间" :span="2">
          <span class="mono">{{ new Date(health.timestamp).toLocaleString() }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-else-if="healthErr"
        type="error"
        :closable="false"
        :title="'健康检查失败: ' + healthErr"
      />
      <el-skeleton v-else :rows="2" animated />
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-5;
}

.hello {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin: 0;
}

.cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;
  margin-bottom: $space-5;

  @include respond-to(md) {
    grid-template-columns: repeat(2, 1fr);
  }

  @include respond-to(xl) {
    grid-template-columns: repeat(4, 1fr);
  }
}

.stat-card {
  transition: transform 0.15s ease;

  &:hover {
    transform: translateY(-2px);
  }
}

.stat {
  display: flex;
  align-items: center;
  gap: $space-3;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: $radius-md;
  @include flex-center;
  color: #fff;

  &.prompt { background: linear-gradient(135deg, #4f8cff, #6ba0ff); }
  &.key    { background: linear-gradient(135deg, #67c23a, #85ce61); }
  &.memory { background: linear-gradient(135deg, #e6a23c, #ebb563); }
  &.log    { background: linear-gradient(135deg, #909399, #a6a9ad); }
}

.stat-body { min-width: 0; }

.stat-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-top: 2px;
}

.stat-total {
  font-size: 14px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.stat-footer {
  margin-top: $space-3;
  padding-top: $space-2;
  border-top: 1px dashed var(--el-border-color-lighter);
  text-align: right;
}

.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
}
</style>
