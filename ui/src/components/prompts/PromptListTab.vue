<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh } from '@element-plus/icons-vue'
import { listPrompts, resetPrompt } from '@/api/client'
import type { PromptSummary } from '@/types/api'

const emit = defineEmits<{
  edit: [name: string]
}>()

const prompts = ref<PromptSummary[]>([])
const loading = ref(false)
const query = ref('')

const filtered = computed<PromptSummary[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return prompts.value
  return prompts.value.filter(
    (p) => p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q),
  )
})

async function refresh() {
  loading.value = true
  try {
    prompts.value = await listPrompts()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function onEdit(row: PromptSummary) {
  emit('edit', row.name)
}

async function onReset(row: PromptSummary) {
  if (!row.overridden) {
    ElMessage.info('该提示词未覆盖, 无需重置')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认重置 "${row.name}" 为默认版本？当前覆盖将被移动到 .history 目录。`,
      '重置提示词',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const updated = await resetPrompt(row.name)
    const idx = prompts.value.findIndex((p) => p.name === row.name)
    if (idx >= 0) prompts.value[idx] = updated
    ElMessage.success('已重置为默认版本')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">提示词管理</h3>
        <p class="tab-subtitle">
          管理系统内建的 Agent 提示词。修改后的版本存于 <span class="mono">data/prompts/</span>,
          随时可重置为默认。
        </p>
      </div>
      <div class="head-actions">
        <el-input v-model="query" placeholder="搜索名称或描述" clearable class="search">
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="filtered"
        stripe
        row-key="name"
        empty-text="暂无提示词"
      >
        <el-table-column prop="name" label="名称" min-width="200">
          <template #default="{ row }">
            <span class="mono name-cell">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="description"
          label="描述"
          min-width="260"
          show-overflow-tooltip
        />
        <el-table-column label="占位符" min-width="200">
          <template #default="{ row }">
            <div class="tags">
              <el-tag
                v-for="ph in row.placeholders"
                :key="ph"
                size="small"
                type="info"
                class="mono"
              >
                {{ ph }}
              </el-tag>
              <span v-if="!row.placeholders.length" class="muted">—</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.overridden" type="warning" size="small">已覆盖</el-tag>
            <el-tag v-else type="success" size="small">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本" prop="version" width="80" align="center" />
        <el-table-column label="操作" width="200" align="right" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="onEdit(row)">编辑</el-button>
            <el-button
              link
              type="danger"
              :disabled="!row.overridden"
              @click="onReset(row)"
            >
              重置
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
}

.tab-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 $space-1;
}

.tab-subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.head-actions {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.search {
  width: 240px;
}

.name-cell {
  font-weight: 500;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
