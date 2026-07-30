<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import { listInteractions, listConversationTurns } from '@/api/client'
import type { InteractionSummary, ConversationTurn } from '@/types/api'

const props = defineProps<{
  active?: boolean
}>()

const interactions = ref<InteractionSummary[]>([])
const loading = ref(false)
const expandedId = ref<string | null>(null)
const interactionTurns = ref<ConversationTurn[]>([])
const turnsLoading = ref(false)

async function refresh() {
  loading.value = true
  try {
    const res = await listInteractions(30)
    interactions.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function toggleExpand(item: InteractionSummary) {
  if (expandedId.value === item.interaction_id) {
    expandedId.value = null
    return
  }
  expandedId.value = item.interaction_id
  turnsLoading.value = true
  try {
    const res = await listConversationTurns({
      interaction_id: item.interaction_id,
      page_size: 100,
      sort_by: 'ts',
      sort_order: 'asc',
    })
    interactionTurns.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    turnsLoading.value = false
  }
}

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}

function eventTypeTag(type: string): 'info' | 'warning' | 'success' {
  if (type === 'tool_call') return 'warning'
  if (type === 'tool_result') return 'success'
  return 'info'
}

function eventTypeLabel(type: string): string {
  if (type === 'tool_call') return '工具调用'
  if (type === 'tool_result') return '工具结果'
  return '消息'
}

watch(
  () => props.active,
  (v) => {
    if (v && !interactions.value.length) refresh()
  },
  { immediate: true },
)
</script>

<template>
  <div class="interaction-list">
    <el-card class="interaction-card">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <span>逻辑交互事务</span>
            <el-tag size="small" type="info">共 {{ interactions.length }} 个</el-tag>
            <span class="hint">
              同一用户输入触发的多轮 HTTP 请求 (工具调用 → 结果 → 最终回复) 按交互 ID 聚合
            </span>
          </div>
          <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
        </div>
      </template>

      <div v-loading="loading" class="interaction-items">
        <div
          v-for="item in interactions"
          :key="item.interaction_id"
          class="interaction-item"
        >
          <div class="item-head" @click="toggleExpand(item)">
            <el-icon class="caret">
              <ArrowDown v-if="expandedId === item.interaction_id" />
              <ArrowRight v-else />
            </el-icon>
            <code class="iid">{{ item.interaction_id.slice(0, 24) }}</code>
            <el-tag v-if="item.has_tool_calls" type="warning" size="small">含工具调用</el-tag>
            <span class="meta">{{ item.event_count }} 事件</span>
            <span class="ts">{{ fmtTs(item.last_ts) }}</span>
          </div>

          <div v-if="expandedId === item.interaction_id" class="item-detail">
            <div v-if="turnsLoading" class="loading">加载中…</div>
            <template v-else>
              <div
                v-for="t in interactionTurns"
                :key="t.id"
                class="turn-row"
              >
                <el-tag :type="eventTypeTag(t.event_type)" size="small">
                  {{ eventTypeLabel(t.event_type) }}
                </el-tag>
                <span class="turn-role">{{ t.role }}</span>
                <span v-if="t.tool_name" class="turn-tool">🔧 {{ t.tool_name }}</span>
                <span class="turn-content">{{ t.content.slice(0, 120) }}{{ t.content.length > 120 ? '…' : '' }}</span>
                <span class="turn-ts">{{ fmtTs(t.ts) }}</span>
              </div>
              <div v-if="!interactionTurns.length" class="empty">无事件</div>
            </template>
          </div>
        </div>
        <div v-if="!interactions.length && !loading" class="empty">
          还没有工具交互记录。触发一次工具调用后再刷新。
        </div>
      </div>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.interaction-card {
  .card-header {
    display: flex;
    align-items: center;
    gap: $space-3;
    .header-left {
      display: flex;
      align-items: center;
      gap: $space-2;
      flex: 1;
    }
    .hint {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }
}

.interaction-items {
  display: flex;
  flex-direction: column;
  gap: $space-1;
}

.interaction-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  overflow: hidden;
}

.item-head {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  cursor: pointer;
  font-size: 12px;
  font-family: 'JetBrains Mono', Menlo, monospace;

  &:hover { background: var(--el-fill-color); }

  .caret { color: var(--el-text-color-secondary); }
  .iid { color: var(--el-text-color-primary); }
  .meta { color: var(--el-text-color-secondary); }
  .ts { margin-left: auto; color: var(--el-text-color-secondary); }
}

.item-detail {
  padding: $space-2 $space-3;
  background: var(--el-fill-color-lighter);
  border-top: 1px solid var(--el-border-color-lighter);
}

.turn-row {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: 4px 0;
  font-size: 12px;
  font-family: 'JetBrains Mono', Menlo, monospace;
  border-bottom: 1px solid var(--el-border-color-extra-light);

  &:last-child { border-bottom: 0; }

  .turn-role { color: var(--el-text-color-secondary); min-width: 40px; }
  .turn-tool { color: var(--el-color-warning); }
  .turn-content {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--el-text-color-primary);
  }
  .turn-ts { color: var(--el-text-color-secondary); font-size: 11px; }
}

.loading, .empty {
  color: var(--el-text-color-secondary);
  padding: $space-3;
  text-align: center;
}
</style>
