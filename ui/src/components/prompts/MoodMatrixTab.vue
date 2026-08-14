<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteMoodMatrixCell,
  getMoodMatrix,
  putMoodMatrixCell,
} from '@/api/prompts'
import type { MoodMatrixCell } from '@/api/prompts'

interface TierRow {
  id: string
  label: string
  cells: MoodMatrixCell[]
}

interface MatrixRow {
  tierId: string
  tierLabel: string
  [moodCol: string]: string | MoodMatrixCell | undefined
}

const loading = ref(false)
const tiers = ref<TierRow[]>([])
const moodLabels = ref<string[]>([])

const rows = computed(() => {
  // el-table 需要每格唯一 key, 按心情 idx 生成可读行
  return tiers.value.map((t) => {
    const row: MatrixRow = {
      tierId: t.id,
      tierLabel: t.label,
    }
    moodLabels.value.forEach((m, i) => {
      row[`m${i}`] = t.cells.find((c) => c.id === `${t.id}_${m}`)
    })
    return row
  })
})

function colKey(i: number): string {
  return `m${i}`
}

function cellId(row: MatrixRow): string {
  const label = moodLabels.value[0]
  void label
  return `${row.tierId}_x`
}

function summarize(text: string): string {
  return text.length > 30 ? text.slice(0, 30) + '…' : text
}

async function refresh() {
  loading.value = true
  try {
    const res = await getMoodMatrix()
    moodLabels.value = res.mood_labels
    tiers.value = res.favor_tiers.map((f) => ({
      id: f.id,
      label: f.label,
      cells: res.cells.filter((c) => c.id.startsWith(`${f.id}_`)),
    }))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

// ── 编辑对话框 ─────────────────────────────────────────────

const editVisible = ref(false)
const editing = ref<MoodMatrixCell | null>(null)
const editCellId = ref('')
const editText = ref('')
const saving = ref(false)

function openEdit(cell: MoodMatrixCell | undefined, cellId: string) {
  const target = cell ?? { id: cellId, text: '', overridden: false }
  editing.value = target
  editCellId.value = target.id
  editText.value = target.text
  editVisible.value = true
}

async function onSave() {
  if (!editing.value) return
  saving.value = true
  try {
    await putMoodMatrixCell(editCellId.value, editText.value)
    ElMessage.success('已保存')
    editVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function onReset() {
  if (!editing.value || !editing.value.overridden) return
  try {
    await ElMessageBox.confirm(`重置单元格 [${editCellId.value}] 为默认引导文本?`, '重置为默认', {
      confirmButtonText: '重置', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  saving.value = true
  try {
    await deleteMoodMatrixCell(editCellId.value)
    ElMessage.success('已重置为默认')
    editVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

// ── 重置全部自定义 ─────────────────────────────────────────

const resettingAll = ref(false)

async function onResetAll() {
  const overridden = tiers.value.flatMap((t) => t.cells).filter((c) => c.overridden)
  if (overridden.length === 0) {
    ElMessage.info('当前没有自定义覆盖')
    return
  }
  try {
    await ElMessageBox.confirm(
      `重置全部自定义引导文本 (共 ${overridden.length} 格) 为默认值?`,
      '重置全部自定义',
      { confirmButtonText: '重置', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  resettingAll.value = true
  try {
    for (const cell of overridden) {
      await deleteMoodMatrixCell(cell.id)
    }
    ElMessage.success(`已重置 ${overridden.length} 格`)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    resettingAll.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">情绪矩阵</h3>
        <p class="tab-subtitle">
          按「好感度档 × 心情段」配置人格的情绪引导文本。点击任意格子可编辑, 支持重置为默认;
          加粗浅色底纹表示已被自定义覆盖。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh"><el-icon><Refresh /></el-icon><span>刷新</span></el-button>
        <el-button type="warning" plain :loading="resettingAll" @click="onResetAll">重置全部自定义</el-button>
      </div>
    </div>

    <el-table
      :data="rows"
      v-loading="loading"
      stripe
      border
      empty-text="暂无情绪矩阵数据"
    >
      <el-table-column label="好感度档" min-width="110" fixed>
        <template #default="{ row }: { row: Record<string, any> }">
          <b>{{ row.tierLabel }}</b>
        </template>
      </el-table-column>
      <el-table-column v-for="(m, i) in moodLabels" :key="m" :label="m" min-width="180">
        <template #default="{ row }: { row: Record<string, any> }">
          <el-tooltip
            :content="(row[`m${i}`] as MoodMatrixCell)?.text || '(默认)'"
            placement="top"
            :disabled="!row[`m${i}`]?.text"
          >
            <button
              class="cell-btn"
              :class="{ 'cell-overridden': (row[`m${i}`] as MoodMatrixCell)?.overridden }"
              type="button"
              @click="openEdit(row[colKey(i)] as MoodMatrixCell | undefined, cellId(row))"
            >
              <template v-if="row[`m${i}`]?.text">
                <b v-if="(row[`m${i}`] as MoodMatrixCell)?.overridden">{{ summarize((row[`m${i}`] as MoodMatrixCell)!.text) }}</b>
                <template v-else>{{ summarize((row[`m${i}`] as MoodMatrixCell).text) }}</template>
              </template>
              <span v-else class="cell-default">(默认)</span>
            </button>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="`编辑格子: ${editCellId}`" width="640px">
      <p class="hint">输入引导文本, 留空保存 = 恢复为默认。</p>
      <el-input
        v-model="editText"
        type="textarea"
        :rows="6"
        maxlength="2000"
        show-word-limit
        placeholder="留空保存将重置为默认引导文本"
      />
      <template #footer>
        <el-button @click="editVisible = false" :disabled="saving">取消</el-button>
        <el-button
          v-if="editing?.overridden"
          type="warning"
          plain
          :loading="saving"
          @click="onReset"
        >
          重置为默认
        </el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tab-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.tab-title {
  margin: 0 0 4px;
  font-size: 18px;
}

.tab-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.head-actions {
  display: flex;
  gap: 8px;
}

.hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.cell-btn {
  display: block;
  width: 100%;
  padding: 8px;
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.4;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--el-text-color-regular);
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  transition: background-color 0.15s;
}

.cell-btn:hover {
  background: var(--el-fill-color-light);
}

.cell-overridden {
  font-weight: 600;
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-7);
}

.cell-overridden:hover {
  background: var(--el-color-warning-light-8);
}

.cell-default {
  color: var(--el-text-color-placeholder);
}
</style>