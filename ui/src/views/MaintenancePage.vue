<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMemoryReindexStatus,
  pruneMemories,
  resetPersona,
  startMemoryReindex,
} from '@/api/client'
import type {
  PersonaResetResponse,
  PruneResponse,
  ReindexStatusResponse,
} from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import ReindexCard from '@/components/maintenance/ReindexCard.vue'
import PruneCard from '@/components/maintenance/PruneCard.vue'
import ResetCard from '@/components/maintenance/ResetCard.vue'

const status = ref<ReindexStatusResponse>({
  state: 'idle',
  total: 0,
  processed: 0,
  pruned: 0,
  started_at: null,
  finished_at: null,
  error: null,
})

const pollTimer = ref<number | null>(null)

const reindexForm = reactive({
  prune: false,
  priority_threshold: 0.05,
})
const reindexStarting = ref(false)

const pruneForm = reactive({
  priority_threshold: 0.05,
})
const prunePreview = ref<PruneResponse | null>(null)
const previewLoading = ref(false)
const pruneRunning = ref(false)

// Persona reset (v0.2.7) —— 完全重置到"新装"语义
const resetPreview = ref<PersonaResetResponse | null>(null)
const resetPreviewLoading = ref(false)
const resetRunning = ref(false)

const isRunning = computed(() => status.value.state === 'running')

async function fetchStatus() {
  try {
    status.value = await getMemoryReindexStatus()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function startPolling() {
  if (pollTimer.value !== null) return
  pollTimer.value = window.setInterval(fetchStatus, 1500)
}

function stopPolling() {
  if (pollTimer.value !== null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function onStartReindex() {
  try {
    await ElMessageBox.confirm(
      reindexForm.prune
        ? `将重建全部记忆的向量, 并按阈值 ${reindexForm.priority_threshold} 清理低价值记忆。期间新记忆会被拒绝入库。`
        : '将重建全部记忆的向量。期间新记忆会被拒绝入库。',
      '启动 Reindex',
      { type: 'warning', confirmButtonText: '开始', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  reindexStarting.value = true
  try {
    status.value = await startMemoryReindex({
      prune: reindexForm.prune,
      priority_threshold: reindexForm.priority_threshold,
    })
    startPolling()
    ElMessage.success('Reindex 已启动')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    reindexStarting.value = false
  }
}

async function onPreviewPrune() {
  previewLoading.value = true
  try {
    prunePreview.value = await pruneMemories({
      priority_threshold: pruneForm.priority_threshold,
      dry_run: true,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    previewLoading.value = false
  }
}

async function onRunPrune() {
  if (!prunePreview.value || prunePreview.value.would_delete === 0) {
    ElMessage.info('先点『预览』看看会删几条')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将删除 ${prunePreview.value.would_delete} 条记忆 (forgotten=${prunePreview.value.breakdown.forgotten}, expired=${prunePreview.value.breakdown.expired}, low_priority=${prunePreview.value.breakdown.low_priority})。删除后不可恢复。`,
      '确认清理',
      { type: 'warning', confirmButtonText: '清理', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  pruneRunning.value = true
  try {
    const res = await pruneMemories({
      priority_threshold: pruneForm.priority_threshold,
      dry_run: false,
    })
    ElMessage.success(`已删除 ${res.deleted} 条`)
    prunePreview.value = res
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    pruneRunning.value = false
  }
}

async function onPreviewReset() {
  resetPreviewLoading.value = true
  try {
    resetPreview.value = await resetPersona({ dry_run: true })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    resetPreviewLoading.value = false
  }
}

async function onRunReset() {
  const preview = resetPreview.value
  if (!preview) {
    ElMessage.info('先点『预览』看看会清什么')
    return
  }
  const total =
    preview.deleted_memories +
    preview.deleted_relationships +
    preview.deleted_conversation_turns
  try {
    await ElMessageBox.confirm(
      `将清空长期记忆 ${preview.deleted_memories} 条 (含 PERMANENT), 关系 ${preview.deleted_relationships} 条, 短期对话 ${preview.deleted_conversation_turns} 条, 以及向量库。\n\nAPI Key / 服务商 / 提示词 / 模型绑定不会被动。删除后不可恢复。`,
      '确认重置人格状态',
      {
        type: 'error',
        confirmButtonText: '重置',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  resetRunning.value = true
  try {
    const res = await resetPersona({ dry_run: false })
    resetPreview.value = res
    if (res.errors.length > 0) {
      ElMessage.warning(
        `部分失败: ${res.errors.join('; ')}. 已删 memories=${res.deleted_memories}, relationships=${res.deleted_relationships}, turns=${res.deleted_conversation_turns}`,
      )
    } else {
      ElMessage.success(`已重置: 共 ${total} 条状态数据 + 向量库`)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    resetRunning.value = false
  }
}

onMounted(async () => {
  await fetchStatus()
  if (isRunning.value) startPolling()
})

onUnmounted(stopPolling)

// 当 status 转为 success/error, 停止轮询
watch(
  () => status.value.state,
  (s) => {
    if (s !== 'running') stopPolling()
  },
)
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="记忆维护"
      subtitle="Reindex 用于换嵌入模型后重建全部向量; Prune 按衰减规则本地判定 (forgotten / expired / priority &lt; 阈值), PERMANENT 类型永远保留。"
    >
      <template #actions>
        <el-button :loading="false" @click="fetchStatus" :disabled="isRunning && reindexStarting">
          <el-icon><Refresh /></el-icon>
          <span>刷新状态</span>
        </el-button>
      </template>
    </PageHeader>

    <el-row :gutter="16" class="equal-row">
      <el-col :xs="24" :lg="12" class="equal-col">
        <ReindexCard
          :status="status"
          :reindex-form="reindexForm"
          :reindex-starting="reindexStarting"
          @start-reindex="onStartReindex"
        />
      </el-col>

      <el-col :xs="24" :lg="12" class="equal-col">
        <PruneCard
          :prune-form="pruneForm"
          :prune-preview="prunePreview"
          :preview-loading="previewLoading"
          :prune-running="pruneRunning"
          :is-running="isRunning"
          @preview-prune="onPreviewPrune"
          @run-prune="onRunPrune"
        />
      </el-col>
    </el-row>

    <el-alert
      v-if="isRunning"
      type="warning"
      show-icon
      :closable="false"
      style="margin-top: 16px"
    >
      Reindex 进行中: 新记忆写入会被临时拒绝, 检索也可能报锁定错误。等完成后再操作。
    </el-alert>

    <ResetCard
      :reset-preview="resetPreview"
      :reset-preview-loading="resetPreviewLoading"
      :reset-running="resetRunning"
      :is-running="isRunning"
      @preview-reset="onPreviewReset"
      @run-reset="onRunReset"
    />
  </div>
</template>

<style lang="scss" scoped>
.equal-row {
  display: flex;
  flex-wrap: wrap;
}

.equal-col {
  display: flex;
}
</style>
