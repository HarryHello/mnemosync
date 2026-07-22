<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listConversationTurnSources,
  listConversationTurns,
  deleteConversationTurn,
  deleteConversationTurns,
} from '@/api/client'
import type { ConversationTurn } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

const props = defineProps<{
  active?: boolean
}>()

const emit = defineEmits<{}>()



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
const sources = ref<Array<{ text: string; value: string }>>([])
const selectedIds = ref<number[]>([])
const loaded = ref(false)
const detailDrawerVisible = ref(false)
const currentDetailRow = ref<ConversationTurn | null>(null)

const TURN_ROLE_FILTERS = [
  { text: '用户', value: 'user' },
  { text: '助手', value: 'assistant' },
]

function normalizeOrder(order: string | null | undefined): 'asc' | 'desc' | null {
  if (order === 'ascending') return 'asc'
  if (order === 'descending') return 'desc'
  return null
}

function toElOrder(o: 'asc' | 'desc'): 'ascending' | 'descending' {
  return o === 'asc' ? 'ascending' : 'descending'
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

function onSelectionChange(selection: ConversationTurn[]) {
  selectedIds.value = selection.map((item) => item.id)
}

async function refreshSources() {
  try {
    const res = await listConversationTurnSources()
    sources.value = res.items.map((v) => ({ text: v, value: v }))
  } catch {
    sources.value = []
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
  if (r !== undefined) roleFilter.value = r ?? []
  if (s !== undefined) sourceFilter.value = s ?? []
  page.value = 1
  refresh()
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

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

function contentPreview(content: string): string {
  const firstLine = content.split('\n')[0] || ''
  if (firstLine.length > 100) {
    return firstLine.slice(0, 100) + '…'
  }
  if (content.includes('\n')) {
    return firstLine + '…'
  }
  return content
}

function openDetail(row: ConversationTurn): void {
  currentDetailRow.value = row
  detailDrawerVisible.value = true
}

watch(() => props.active, (active) => {
  if (active && !loaded.value) {
    refreshSources()
    refresh()
    loaded.value = true
  }
})
</script>

<template>
  <div class="context-tab">
    <PageHeader
      title="短期记忆（上下文）"
      subtitle="跨前端汇聚的连续对话，供装填上游用。列头可点击排序 / 过滤。"
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

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <span>跨前端上下文</span>
            <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
            <span class="hint">
              服务器视角的连续对话流, 装填时按时间窗 + 模型窗从这里裁剪
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
      <el-table
        v-loading="loading"
        :data="turns"
        stripe
        row-key="id"
        empty-text="暂无对话流水"
        :default-sort="{ prop: sortBy, order: toElOrder(sortOrder) }"
        @sort-change="onSortChange"
        @filter-change="onFilterChange"
        max-height="calc(100vh - 340px)"
      >
        <el-table-column
          label="角色"
          width="120"
          align="center"
          prop="role"
          column-key="role"
          sortable="custom"
          :filters="TURN_ROLE_FILTERS"
          :filter-multiple="false"
          :filtered-value="roleFilter"
        >
          <template #default="{ row }">
            <el-tag :type="roleTag(row.role)" size="small">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="360">
          <template #default="{ row }">
            <el-tooltip :content="row.content" placement="top" :disabled="!row.content">
              <div class="mem-content-preview" @click="openDetail(row)">
                {{ contentPreview(row.content) }}
              </div>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          prop="token_count"
          label="token"
          width="100"
          align="center"
          sortable="custom"
        />
        <el-table-column
          label="来源"
          width="140"
          prop="source_frontend"
          column-key="source_frontend"
          sortable="custom"
          :filters="sources"
          :filter-multiple="false"
          :filtered-value="sourceFilter"
        >
          <template #default="{ row }">
            <span class="mono muted">{{ row.source_frontend || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="时间"
          width="180"
          prop="ts"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono muted">{{ fmtDate(row.ts) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="ID"
          width="90"
          align="center"
          prop="id"
          sortable="custom"
        >
          <template #default="{ row }">
            <span class="mono mini">#{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column width="55" align="center" fixed="right">
          <template #header>
            <el-checkbox
              :model-value="selectedIds.length === turns.length && turns.length > 0"
              :indeterminate="selectedIds.length > 0 && selectedIds.length < turns.length"
              @change="toggleSelectAll"
            />
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="selectedIds.includes(row.id)"
              @change="(val: boolean) => toggleSelect(row.id, val)"
            />
          </template>
        </el-table-column>
      </el-table>

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

    <el-drawer
      v-model="detailDrawerVisible"
      :title="currentDetailRow ? `${roleLabel(currentDetailRow.role)} 消息 #${currentDetailRow.id}` : '详情'"
      size="50%"
    >
      <div v-if="currentDetailRow" class="detail-content">
        <div class="detail-meta">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="角色">
              <el-tag :type="roleTag(currentDetailRow.role)" size="small">
                {{ roleLabel(currentDetailRow.role) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="来源">
              <span class="mono muted">{{ currentDetailRow.source_frontend || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="时间">
              <span class="mono muted">{{ fmtDate(currentDetailRow.ts) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="token">
              {{ currentDetailRow.token_count }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
        <div class="detail-text">
          <div class="detail-text-label">内容</div>
          <pre class="detail-pre">{{ currentDetailRow.content }}</pre>
        </div>
      </div>
    </el-drawer>
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

.mem-content-preview {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  color: var(--el-text-color-regular);
  line-height: 1.5;

  &:hover {
    color: var(--el-color-primary);
  }
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
  padding-top: $space-3;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: $space-4;
}

.detail-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.detail-text-label {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.detail-pre {
  margin: 0;
  padding: $space-3;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-md;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-family: inherit;
  font-size: 14px;
  color: var(--el-text-color-primary);
  max-height: 60vh;
  overflow-y: auto;
}
</style>
