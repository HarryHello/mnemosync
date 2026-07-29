<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listConversationTurnSources,
  listConversationTurnSpeakers,
  listConversationTurns,
  deleteConversationTurn,
  deleteConversationTurns,
} from '@/api/client'
import type { ConversationTurn } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import ContextTable from './ContextTable.vue'
import ContextDetailDrawer from './ContextDetailDrawer.vue'
import InteractionList from './InteractionList.vue'

const props = defineProps<{
  active?: boolean
}>()

const turns = ref<ConversationTurn[]>([])
const loading = ref(false)
const deleting = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const sortBy = ref('ts')
const sortOrder = ref<'asc' | 'desc'>('desc')
const roleFilter = ref<string[]>([])
const sourceFilter = ref<string[]>([])
const speakerFilter = ref<string[]>([])
const speakerOpts = ref<Array<{ text: string; value: string }>>([])
const sources = ref<Array<{ text: string; value: string }>>([])
const selectedIds = ref<number[]>([])
const loaded = ref(false)
const detailDrawerVisible = ref(false)
const currentDetailRow = ref<ConversationTurn | null>(null)

async function refreshSources() {
  try {
    const res = await listConversationTurnSources()
    sources.value = res.items.map((v) => ({ text: v, value: v }))
  } catch {
    sources.value = []
  }
}

async function loadSpeakerOptions() {
  try {
    const res = await listConversationTurnSpeakers()
    speakerOpts.value = res.items.map((sp) => ({
      text: sp.display_name,
      value: sp.effective_user_id,
    }))
  } catch {
    speakerOpts.value = []
  }
}

async function refresh() {
  loading.value = true
  selectedIds.value = []
  try {
    const res = await listConversationTurns({
      page: page.value,
      page_size: pageSize.value,
      role: (roleFilter.value[0] as 'user' | 'assistant' | undefined) || undefined,
      source_frontend: sourceFilter.value[0] || undefined,
      effective_user_id: speakerFilter.value[0] || undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    })
    turns.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onDeleteSelected() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择要删除的记录')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedIds.value.length} 条记录？删除后不可恢复。`,
      '删除选中',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteConversationTurns(selectedIds.value)
    ElMessage.success('已删除')
    if (turns.value.length === selectedIds.value.length && page.value > 1) {
      page.value -= 1
    }
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    deleting.value = false
  }
}

async function onDelete(row: ConversationTurn) {
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
    if (turns.value.length === 1 && page.value > 1) {
      page.value -= 1
    }
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function onPageChange(p: number) {
  page.value = p
  refresh()
}

function onPageSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  refresh()
}

function onSortChange(evt: { prop: string | null; order: string | null }) {
  function normalizeOrder(order: string | null | undefined): 'asc' | 'desc' | null {
    if (order === 'ascending') return 'asc'
    if (order === 'descending') return 'desc'
    return null
  }
  const order = normalizeOrder(evt.order)
  if (!evt.prop || !order) {
    sortBy.value = 'ts'
    sortOrder.value = 'desc'
  } else {
    sortBy.value = evt.prop
    sortOrder.value = order
  }
  page.value = 1
  refresh()
}

function onFilterChange(filters: Record<string, unknown[]>) {
  const r = filters['role'] as string[] | undefined
  const s = filters['source_frontend'] as string[] | undefined
  const sp = filters['effective_user_id'] as string[] | undefined
  if (r !== undefined) roleFilter.value = r ?? []
  if (s !== undefined) sourceFilter.value = s ?? []
  if (sp !== undefined) speakerFilter.value = sp ?? []
  page.value = 1
  refresh()
}

function toggleSelect(id: number, selected: boolean): void {
  if (selected) {
    selectedIds.value = [...selectedIds.value, id]
  } else {
    selectedIds.value = selectedIds.value.filter((i) => i !== id)
  }
}

function toggleSelectAll(selected: boolean): void {
  if (selected) {
    selectedIds.value = turns.value.map((t) => t.id)
  } else {
    selectedIds.value = []
  }
}

function openDetail(row: ConversationTurn): void {
  currentDetailRow.value = row
  detailDrawerVisible.value = true
}

watch(() => props.active, (active) => {
  if (active && !loaded.value) {
    refreshSources()
    loadSpeakerOptions()
    refresh()
    loaded.value = true
  }
})
</script>

<template>
  <div class="context-tab">
    <PageHeader
      title="短期记忆（上下文）"
      subtitle="按说话者拆分的结构化事件流，历史快照会去重并保留平台身份、空间与事件时间。"
    >
      <template #actions>
        <el-button
          :loading="loading"
          @click="() => { refreshSources(); refresh(); }"
        >
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <el-card class="context-card">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <span>跨前端上下文</span>
            <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
            <span class="hint">
              服务器规范化事件流，按空间隔离并在装填模型前重新编译说话者身份
            </span>
          </div>
          <div class="header-right">
            <el-button
              type="danger"
              plain
              :disabled="selectedIds.length === 0"
              :loading="deleting"
              @click="onDeleteSelected"
            >
              <el-icon><Delete /></el-icon>
              <span>删除已选 ({{ selectedIds.length }})</span>
            </el-button>
          </div>
        </div>
      </template>
      <ContextTable
        :items="turns"
        :loading="loading"
        :sort-by="sortBy"
        :sort-order="sortOrder"
        :role-filter="roleFilter"
        :source-filter="sourceFilter"
        :speaker-filter="speakerFilter"
        :speaker-opts="speakerOpts"
        :selected-ids="selectedIds"
        :sources="sources"
        @delete="onDelete"
        @open-detail="openDetail"
        @sort-change="onSortChange"
        @filter-change="onFilterChange"
        @select="toggleSelect"
        @select-all="toggleSelectAll"
      />
      <div class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100, 200]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>

    <InteractionList :active="active" />

    <ContextDetailDrawer v-model="detailDrawerVisible" :item="currentDetailRow" />
  </div>
</template>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-4;
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;
}

.header-right {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: $space-3;
}

.context-card + .interaction-list {
  margin-top: $space-4;
}
</style>
