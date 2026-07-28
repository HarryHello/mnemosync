<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory, deleteMemoriesBatch } from '@/api/client'
import type { Memory } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import MemoryTable from './MemoryTable.vue'
import MemoryDetailDrawer from './MemoryDetailDrawer.vue'

const props = defineProps<{
  active?: boolean
}>()

const memories = ref<Memory[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const sortBy = ref('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')
const typeFilter = ref<string[]>([])
const sourceUser = ref('default')
const loaded = ref(false)
const detailDrawerVisible = ref(false)
const currentDetailRow = ref<Memory | null>(null)

async function refresh() {
  loading.value = true
  try {
    const res = await listMemories({
      source_user: sourceUser.value || 'default',
      page: page.value,
      page_size: pageSize.value,
      memory_type: typeFilter.value[0] || undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    })
    memories.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onDelete(row: Memory) {
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
    if (memories.value.length === 1 && page.value > 1) {
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

function onSourceApply() {
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
    sortBy.value = 'created_at'
    sortOrder.value = 'desc'
  } else {
    sortBy.value = evt.prop
    sortOrder.value = order
  }
  page.value = 1
  refresh()
}

function onFilterChange(filters: Record<string, unknown[]>) {
  const t = filters['memory_type'] as string[] | undefined
  typeFilter.value = t ?? []
  page.value = 1
  refresh()
}

async function onBatchDelete() {
  const type = typeFilter.value[0]
  const typeLabel = type === 'permanent' ? '永久记忆' : type === 'normal' ? '普通记忆' : '全部记忆'
  try {
    await ElMessageBox.confirm(
      `确认删除用户 ${sourceUser.value} 的${typeLabel}？此操作不可恢复。共 ${total.value} 条匹配。`,
      '批量删除记忆',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await deleteMemoriesBatch({
      source_user: sourceUser.value || 'default',
      memory_type: type as 'permanent' | 'normal' | undefined,
    })
    ElMessage.success(`已删除 ${res.deleted} 条记忆`)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function openDetail(row: Memory) {
  currentDetailRow.value = row
  detailDrawerVisible.value = true
}

watch(() => props.active, (active) => {
  if (active && !loaded.value) {
    refresh()
    loaded.value = true
  }
}, { immediate: true })
</script>

<template>
  <div class="memories-tab">
    <PageHeader
      title="长期记忆"
      subtitle="按重要度/衰减规则汰换，列头可点击排序 / 过滤。"
    >
      <template #actions>
        <el-popconfirm
          title="确认批量删除当前筛选条件下的所有记忆？"
          confirm-button-text="删除"
          cancel-button-text="取消"
          @confirm="onBatchDelete"
        >
          <template #reference>
            <el-button type="danger" plain :disabled="total === 0" :loading="loading">
              <el-icon><Delete /></el-icon>
              <span>批量删除</span>
            </el-button>
          </template>
        </el-popconfirm>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <el-card class="memories-card">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <span>长期记忆条目</span>
            <el-tag size="small" type="info">共 {{ total }} 条</el-tag>
          </div>
          <div class="header-right">
            <el-form :inline="true" @submit.prevent="onSourceApply">
              <el-form-item label="source_user">
                <el-input
                  v-model="sourceUser"
                  placeholder="default"
                  clearable
                  style="width: 180px"
                  @keyup.enter="onSourceApply"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="onSourceApply">
                  <el-icon><Search /></el-icon>
                  <span>查询</span>
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </div>
      </template>
      <MemoryTable
        :items="memories"
        :loading="loading"
        :sort-by="sortBy"
        :sort-order="sortOrder"
        :type-filter="typeFilter"
        @delete="onDelete"
        @open-detail="openDetail"
        @sort-change="onSortChange"
        @filter-change="onFilterChange"
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

    <MemoryDetailDrawer v-model="detailDrawerVisible" :item="currentDetailRow" />
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
}

.header-right {
  :deep(.el-form-item) {
    margin-bottom: 0;
  }
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: $space-3;
}
</style>
