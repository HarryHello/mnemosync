<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory } from '@/api/client'
import type { Memory } from '@/types/api'

const memories = ref<Memory[]>([])
const loading = ref(false)
const sourceUser = ref('default')
const query = ref('')
const typeFilter = ref('')

const TYPES = [
  { label: '普通', value: 'normal' },
  { label: '永久', value: 'permanent' },
]

const filtered = computed<Memory[]>(() => {
  const q = query.value.trim().toLowerCase()
  return memories.value.filter((m) => {
    if (typeFilter.value && m.memory_type !== typeFilter.value) return false
    if (q && !m.content.toLowerCase().includes(q)) return false
    return true
  })
})

async function refresh() {
  loading.value = true
  try {
    const res = await listMemories(sourceUser.value || 'default')
    memories.value = res.items
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
    memories.value = memories.value.filter((m) => m.id !== row.id)
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
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

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">记忆管理</h2>
        <p class="page-subtitle">
          按来源用户查看长期/短期记忆条目。重要度越高、访问次数越多越难被衰减淘汰。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="filters">
      <el-form :inline="true" @submit.prevent="refresh">
        <el-form-item label="source_user">
          <el-input
            v-model="sourceUser"
            placeholder="default"
            clearable
            style="width: 180px"
            @keyup.enter="refresh"
          />
        </el-form-item>
        <el-form-item label="类型">
          <el-select
            v-model="typeFilter"
            placeholder="全部"
            clearable
            style="width: 140px"
          >
            <el-option
              v-for="t in TYPES"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="内容搜索">
          <el-input
            v-model="query"
            placeholder="按内容过滤"
            clearable
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="refresh">
            <el-icon><Search /></el-icon>
            <span>加载</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>记忆条目</span>
          <el-tag size="small" type="info">共 {{ filtered.length }} / {{ memories.length }} 条</el-tag>
        </div>
      </template>
      <el-table
        v-loading="loading"
        :data="filtered"
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
            <el-button link type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
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

.head-actions {
  display: flex;
  gap: $space-2;
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
</style>
