<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  clearConversationTurns,
  deleteConversationTurn,
  deleteMemory,
  listConversationTurns,
  listMemories,
} from '@/api/client'
import type { ConversationTurn, Memory } from '@/types/api'

// ============================================================================
// 通用状态
// ============================================================================

const activeTab = ref<'memories' | 'context'>('memories')

// ============================================================================
// Tab 1: 长期记忆
// ============================================================================

const memories = ref<Memory[]>([])
const memoryLoading = ref(false)
const memoryTotal = ref(0)
const memoryPage = ref(1)
const memoryPageSize = ref(10)
const sourceUser = ref('default')
const typeFilter = ref('')

const TYPES = [
  { label: '普通', value: 'normal' },
  { label: '永久', value: 'permanent' },
]

async function refreshMemories() {
  memoryLoading.value = true
  try {
    const res = await listMemories({
      source_user: sourceUser.value || 'default',
      page: memoryPage.value,
      page_size: memoryPageSize.value,
      memory_type: typeFilter.value || undefined,
    })
    memories.value = res.items
    memoryTotal.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    memoryLoading.value = false
  }
}

async function onDeleteMemory(row: Memory) {
  try {
    await ElMessageBox.confirm(
      `确认删除此记忆？删除后不可恢复。\n\n${row.content.slice(0, 80)}${row.content.length > 80 ? '…' : ''}`,
      '删除记忆',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteMemory(row.id)
    ElMessage.success('已删除')
    // 删掉最后一条时回退一页
    if (memories.value.length === 1 && memoryPage.value > 1) {
      memoryPage.value -= 1
    }
    await refreshMemories()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function onMemoryPageChange(p: number) {
  memoryPage.value = p
  refreshMemories()
}

function onMemoryPageSizeChange(s: number) {
  memoryPageSize.value = s
  memoryPage.value = 1
  refreshMemories()
}

function onMemoryFilterApply() {
  memoryPage.value = 1
  refreshMemories()
}

function typeTag(t: string): 'success' | 'primary' | 'info' {
  if (t === 'permanent') return 'success'
  if (t === 'normal') return 'primary'
  return 'info'
}

function typeLabel(t: string): string {
  if (t === 'permanent') return '永久'
  if (t === 'normal') return '普通'
  return t
}

// ============================================================================
// Tab 2: 上下文流水 (conversation_turns)
// ============================================================================

const turns = ref<ConversationTurn[]>([])
const turnLoading = ref(false)
const turnTotal = ref(0)
const turnPage = ref(1)
const turnPageSize = ref(10)
const turnRoleFilter = ref<'' | 'user' | 'assistant'>('')
const turnsLoaded = ref(false)

const ROLES = [
  { label: '用户', value: 'user' },
  { label: '助手', value: 'assistant' },
]

async function refreshTurns() {
  turnLoading.value = true
  try {
    const res = await listConversationTurns({
      page: turnPage.value,
      page_size: turnPageSize.value,
      role: turnRoleFilter.value || undefined,
    })
    turns.value = res.items
    turnTotal.value = res.total
    turnsLoaded.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    turnLoading.value = false
  }
}

async function onDeleteTurn(row: ConversationTurn) {
  try {
    await ElMessageBox.confirm(
      `确认删除这条 ${row.role === 'user' ? '用户' : '助手'} 消息？删除后服务器视角下这条上下文将不存在。\n\n${row.content.slice(0, 80)}${row.content.length > 80 ? '…' : ''}`,
      '删除对话轮次',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteConversationTurn(row.id)
    ElMessage.success('已删除')
    if (turns.value.length === 1 && turnPage.value > 1) {
      turnPage.value -= 1
    }
    await refreshTurns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onClearAllTurns() {
  try {
    await ElMessageBox.confirm(
      `将清空跨前端上下文流水共 ${turnTotal.value} 条 (不区分 role)。删除后不可恢复。\n\n注意: 这只清服务器视角的连续记忆, 各前端自己的显示状态不受影响。`,
      '清空全部上下文',
      { type: 'error', confirmButtonText: '全部清空', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await clearConversationTurns()
    ElMessage.success(`已清空 ${res.deleted} 条`)
    turnPage.value = 1
    await refreshTurns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function onTurnPageChange(p: number) {
  turnPage.value = p
  refreshTurns()
}

function onTurnPageSizeChange(s: number) {
  turnPageSize.value = s
  turnPage.value = 1
  refreshTurns()
}

function onTurnFilterApply() {
  turnPage.value = 1
  refreshTurns()
}

function roleTag(r: string): 'primary' | 'success' | 'info' {
  if (r === 'user') return 'primary'
  if (r === 'assistant') return 'success'
  return 'info'
}

function roleLabel(r: string): string {
  if (r === 'user') return '用户'
  if (r === 'assistant') return '助手'
  return r
}

// ============================================================================
// 通用
// ============================================================================

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

// 首次切到 context 标签时懒加载
watch(activeTab, (t) => {
  if (t === 'context' && !turnsLoaded.value) {
    refreshTurns()
  }
})

onMounted(refreshMemories)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">记忆管理</h2>
        <p class="page-subtitle">
          长期记忆按重要度/衰减规则汰换; 上下文流水是跨前端汇聚的连续对话, 供装填上游用。
        </p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="mem-tabs">
      <!-- ================================================================ -->
      <!-- Tab 1: 长期记忆                                                    -->
      <!-- ================================================================ -->
      <el-tab-pane label="长期记忆" name="memories">
        <el-card shadow="never" class="filters">
          <el-form :inline="true" @submit.prevent="onMemoryFilterApply">
            <el-form-item label="source_user">
              <el-input
                v-model="sourceUser"
                placeholder="default"
                clearable
                style="width: 180px"
                @keyup.enter="onMemoryFilterApply"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="typeFilter"
                placeholder="全部"
                clearable
                style="width: 140px"
                @change="onMemoryFilterApply"
              >
                <el-option
                  v-for="t in TYPES"
                  :key="t.value"
                  :label="t.label"
                  :value="t.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="onMemoryFilterApply">
                <el-icon><Search /></el-icon>
                <span>查询</span>
              </el-button>
              <el-button :loading="memoryLoading" @click="refreshMemories">
                <el-icon><Refresh /></el-icon>
                <span>刷新</span>
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>长期记忆条目</span>
              <el-tag size="small" type="info">共 {{ memoryTotal }} 条</el-tag>
            </div>
          </template>
          <el-table
            v-loading="memoryLoading"
            :data="memories"
            stripe
            row-key="id"
            empty-text="暂无记忆"
          >
            <el-table-column label="内容" min-width="360">
              <template #default="{ row }">
                <div class="mem-content">{{ row.content }}</div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="typeTag(row.memory_type)" size="small">
                  {{ typeLabel(row.memory_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="重要度" width="110" align="center">
              <template #default="{ row }">
                <el-progress
                  :percentage="Math.round(row.importance * 100)"
                  :stroke-width="6"
                  :show-text="false"
                />
                <div class="mono mini">{{ row.importance.toFixed(2) }}</div>
              </template>
            </el-table-column>
            <el-table-column
              prop="access_count"
              label="访问次数"
              width="90"
              align="center"
            />
            <el-table-column label="来源" width="110">
              <template #default="{ row }">
                <span class="mono muted">{{ row.source_user || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="170">
              <template #default="{ row }">
                <span class="mono muted">{{ fmtDate(row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="最近访问" width="170">
              <template #default="{ row }">
                <span class="mono muted">{{ fmtDate(row.last_accessed_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="right" fixed="right">
              <template #default="{ row }">
                <el-button link type="danger" @click="onDeleteMemory(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination
              :current-page="memoryPage"
              :page-size="memoryPageSize"
              :page-sizes="[10, 20, 50, 100, 200]"
              :total="memoryTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="onMemoryPageChange"
              @size-change="onMemoryPageSizeChange"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ================================================================ -->
      <!-- Tab 2: 上下文流水                                                  -->
      <!-- ================================================================ -->
      <el-tab-pane label="上下文流水" name="context">
        <el-card shadow="never" class="filters">
          <el-form :inline="true" @submit.prevent="onTurnFilterApply">
            <el-form-item label="角色">
              <el-select
                v-model="turnRoleFilter"
                placeholder="全部"
                clearable
                style="width: 140px"
                @change="onTurnFilterApply"
              >
                <el-option
                  v-for="r in ROLES"
                  :key="r.value"
                  :label="r.label"
                  :value="r.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button :loading="turnLoading" @click="refreshTurns">
                <el-icon><Refresh /></el-icon>
                <span>刷新</span>
              </el-button>
              <el-button
                type="danger"
                plain
                :disabled="turnLoading || turnTotal === 0"
                @click="onClearAllTurns"
              >
                清空全部
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>跨前端上下文</span>
              <el-tag size="small" type="info">共 {{ turnTotal }} 条</el-tag>
              <span class="hint">
                服务器视角的连续对话流, 装填时按时间窗 + 模型窗从这里裁剪
              </span>
            </div>
          </template>
          <el-table
            v-loading="turnLoading"
            :data="turns"
            stripe
            row-key="id"
            empty-text="暂无对话流水"
          >
            <el-table-column label="角色" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="roleTag(row.role)" size="small">
                  {{ roleLabel(row.role) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="内容" min-width="360">
              <template #default="{ row }">
                <div class="mem-content">{{ row.content }}</div>
              </template>
            </el-table-column>
            <el-table-column
              prop="token_count"
              label="token"
              width="80"
              align="center"
            />
            <el-table-column label="来源" width="130">
              <template #default="{ row }">
                <span class="mono muted">{{ row.source_frontend || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="170">
              <template #default="{ row }">
                <span class="mono muted">{{ fmtDate(row.ts) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="right" fixed="right">
              <template #default="{ row }">
                <el-button link type="danger" @click="onDeleteTurn(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination
              :current-page="turnPage"
              :page-size="turnPageSize"
              :page-sizes="[10, 20, 50, 100, 200]"
              :total="turnTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="onTurnPageChange"
              @size-change="onTurnPageSizeChange"
            />
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style lang="scss" scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
}

.mem-tabs {
  :deep(.el-tabs__content) {
    padding-top: $space-3;
  }
}

.filters {
  margin-bottom: $space-4;

  :deep(.el-form-item) {
    margin-bottom: 0;
  }
}

.card-header {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mem-content {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

.muted {
  color: var(--el-text-color-secondary);
}

.mini {
  font-size: 11px;
  margin-top: 2px;
  color: var(--el-text-color-secondary);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: $space-3;
}
</style>
