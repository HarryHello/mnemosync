<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
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

const progressPct = computed(() => {
  if (!status.value.total) return 0
  return Math.min(100, Math.floor((status.value.processed / status.value.total) * 100))
})

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

function fmtTime(s: string | null): string {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleString()
  } catch {
    return s
  }
}

onMounted(async () => {
  await fetchStatus()
  if (isRunning.value) startPolling()
})

onUnmounted(stopPolling)

// 当 status 转为 success/error, 停止轮询
import { watch } from 'vue'
watch(
  () => status.value.state,
  (s) => {
    if (s !== 'running') stopPolling()
  },
)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">记忆维护</h2>
        <p class="page-subtitle">
          Reindex 用于换嵌入模型后重建全部向量;
          Prune 按衰减规则本地判定 (forgotten / expired / priority &lt; 阈值), PERMANENT 类型永远保留。
        </p>
      </div>
    </div>

    <el-row :gutter="16" class="equal-row">
      <el-col :xs="24" :lg="12" class="equal-col">
        <el-card shadow="hover" class="section equal-card">
          <template #header>
            <div class="sec-head">
              <span class="sec-title">重建记忆向量库 (Reindex)</span>
              <el-tag :type="{
                idle: 'info',
                running: 'warning',
                success: 'success',
                error: 'danger',
              }[status.state]" size="small">
                {{ status.state }}
              </el-tag>
            </div>
          </template>

          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="进度">
              <el-progress
                :percentage="progressPct"
                :status="status.state === 'error' ? 'exception' : (status.state === 'success' ? 'success' : undefined)"
              />
              <div class="hint">
                {{ status.processed }} / {{ status.total }} · pruned {{ status.pruned }}
              </div>
            </el-descriptions-item>
            <el-descriptions-item label="开始">
              {{ fmtTime(status.started_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="结束">
              {{ fmtTime(status.finished_at) }}
            </el-descriptions-item>
            <el-descriptions-item v-if="status.error" label="错误">
              <span class="err">{{ status.error }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <el-divider />

          <el-form label-width="120px" size="default">
            <el-form-item label="顺便清理">
              <el-switch v-model="reindexForm.prune" :disabled="isRunning" />
              <span class="hint">遍历时按下方阈值清理低价值记忆</span>
            </el-form-item>
            <el-form-item v-if="reindexForm.prune" label="优先级阈值">
              <el-input-number
                v-model="reindexForm.priority_threshold"
                :min="0.01"
                :max="0.5"
                :step="0.01"
                :precision="2"
                :disabled="isRunning"
              />
              <span class="hint">theoretical_priority &lt; 阈值 且非 PERMANENT 才会被清理</span>
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :loading="reindexStarting"
                :disabled="isRunning"
                @click="onStartReindex"
              >
                {{ isRunning ? '进行中…' : '启动' }}
              </el-button>
              <el-button @click="fetchStatus" :disabled="isRunning && reindexStarting">
                刷新状态
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12" class="equal-col">
        <el-card shadow="hover" class="section equal-card">
          <template #header>
            <div class="sec-head">
              <span class="sec-title">清理低价值记忆 (Prune)</span>
            </div>
          </template>

          <el-form label-width="120px" size="default">
            <el-form-item label="优先级阈值">
              <el-input-number
                v-model="pruneForm.priority_threshold"
                :min="0.01"
                :max="0.5"
                :step="0.01"
                :precision="2"
              />
              <span class="hint">同 Reindex 阈值语义</span>
            </el-form-item>
            <el-form-item>
              <el-button
                :loading="previewLoading"
                :disabled="isRunning"
                @click="onPreviewPrune"
              >
                预览
              </el-button>
              <el-button
                type="danger"
                :loading="pruneRunning"
                :disabled="isRunning || !prunePreview || prunePreview.would_delete === 0"
                @click="onRunPrune"
              >
                执行清理
              </el-button>
            </el-form-item>
          </el-form>

          <el-divider v-if="prunePreview" />

          <el-descriptions v-if="prunePreview" :column="1" size="small" border>
            <el-descriptions-item label="总数 (预览前)">
              {{ prunePreview.total_before }}
            </el-descriptions-item>
            <el-descriptions-item label="预计删除">
              {{ prunePreview.would_delete }}
            </el-descriptions-item>
            <el-descriptions-item label="实际已删">
              {{ prunePreview.deleted }}
            </el-descriptions-item>
            <el-descriptions-item label="分类">
              <div class="chip-row">
                <span class="meta-chip">forgotten {{ prunePreview.breakdown.forgotten }}</span>
                <span class="meta-chip">expired {{ prunePreview.breakdown.expired }}</span>
                <span class="meta-chip">low_priority {{ prunePreview.breakdown.low_priority }}</span>
              </div>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
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

    <el-card shadow="hover" class="section danger-section" style="margin-top: 16px">
      <template #header>
        <div class="sec-head">
          <span class="sec-title">重置人格状态 (Persona Reset)</span>
          <el-tag type="danger" size="small">危险</el-tag>
        </div>
      </template>

      <p class="reset-desc">
        清空所有长期记忆 (含 PERMANENT) / 关系 (亲密度 · 信任度) / 短期对话流水 / 向量库,
        回到"新装"语义。<b>不会</b>动 API Key / 服务商 / 提示词 / 模型绑定 / 管理员账户。
        删除后不可恢复; 与 Reindex 互斥 (进行中会 409)。
      </p>

      <el-form label-width="120px" size="default">
        <el-form-item>
          <el-button
            :loading="resetPreviewLoading"
            :disabled="isRunning || resetRunning"
            @click="onPreviewReset"
          >
            预览
          </el-button>
          <el-button
            type="danger"
            :loading="resetRunning"
            :disabled="isRunning || !resetPreview"
            @click="onRunReset"
          >
            执行重置
          </el-button>
        </el-form-item>
      </el-form>

      <el-divider v-if="resetPreview" />

      <el-descriptions v-if="resetPreview" :column="1" size="small" border>
        <el-descriptions-item label="将清 长期记忆">
          {{ resetPreview.deleted_memories }} 条 (含 PERMANENT)
        </el-descriptions-item>
        <el-descriptions-item label="将清 关系">
          {{ resetPreview.deleted_relationships }} 条
        </el-descriptions-item>
        <el-descriptions-item label="将清 短期对话">
          {{ resetPreview.deleted_conversation_turns }} 条
        </el-descriptions-item>
        <el-descriptions-item label="向量库">
          {{ resetPreview.vector_reset ? '已重建 collection' : '未触发 (dry-run)' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="resetPreview.errors.length > 0" label="错误">
          <div v-for="err in resetPreview.errors" :key="err" class="err">
            {{ err }}
          </div>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.page-head {
  margin-bottom: $space-4;
}

.section {
  margin-bottom: $space-4;
}

.equal-row {
  display: flex;
  flex-wrap: wrap;
}

.equal-col {
  display: flex;
}

.equal-card {
  width: 100%;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    flex: 1;
  }
}

.sec-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sec-title {
  font-weight: 600;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: $space-2;
}

.err {
  color: var(--el-color-danger);
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}

.chip-row {
  display: flex;
  gap: $space-2;
  flex-wrap: wrap;
}

.meta-chip {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: $radius-sm;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}

.danger-section {
  border: 1px solid var(--el-color-danger-light-7);
}

.reset-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin: 0 0 $space-3 0;
  line-height: 1.6;
}
</style>
