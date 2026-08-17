<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteMoodMatrixCell,
  getMoodMatrix,
  putMoodMatrixCell,
} from '@/api/prompts'
import type { MoodMatrixCell } from '@/api/prompts'

interface MatrixRow {
  tierId: string
  tierLabel: string
  cells: Record<string, MoodMatrixCell>
}

const loading = ref(false)
const tiers = ref<{ id: string; label: string; cells: MoodMatrixCell[] }[]>([])
const moodLabels = ref<{ id: string; label: string }[]>([])

const rows = computed<MatrixRow[]>(() =>
  tiers.value.map((t) => {
    const cells: Record<string, MoodMatrixCell> = {}
    for (const c of t.cells) cells[c.id] = c
    return { tierId: t.id, tierLabel: t.label, cells }
  }),
)

function summarize(text: string): string {
  return text.length > 30 ? text.slice(0, 30) + '…' : text
}

function cellId(tierId: string, mood: string): string {
  return `${tierId}_${mood}`
}

function cellOf(row: MatrixRow, mood: string): MoodMatrixCell | undefined {
  return row.cells[cellId(row.tierId, mood)]
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

function openEdit(cell: MoodMatrixCell | undefined, id: string) {
  editCellId.value = id
  editing.value = cell ?? { id, text: '', overridden: false }
  editText.value = editing.value.text
  editVisible.value = true
}

async function onSave() {
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
          加粗 + 浅色底纹表示已被自定义覆盖, 文本留空表示使用默认。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh"><el-icon><Refresh /></el-icon><span>刷新</span></el-button>
        <el-button type="warning" plain :loading="resettingAll" @click="onResetAll">重置全部自定义</el-button>
      </div>
    </div>

    <el-card>
      <el-table :data="rows" v-loading="loading" stripe empty-text="暂无情绪矩阵数据">
        <el-table-column label=" " min-width="60" fixed>
          <template #default="{ row }: { row: MatrixRow }">
            <b>{{ row.tierLabel }}</b>
          </template>
        </el-table-column>
        <el-table-column v-for="m in moodLabels" :key="m.id" :label="m.label" min-width="180">
          <template #default="{ row }: { row: MatrixRow }">
            <el-tooltip
              :content="cellOf(row, m.id)?.text || '(默认)'"
              placement="top"
              :disabled="!cellOf(row, m.id)?.text"
            >
              <button
                class="cell-btn"
                type="button"
                :class="{ 'cell-overridden': cellOf(row, m.id)?.overridden }"
                @click="openEdit(cellOf(row, m.id), cellId(row.tierId, m.id))"
              >
                <template v-if="cellOf(row, m.id)?.text">
                  <b v-if="cellOf(row, m.id)?.overridden">{{ summarize(cellOf(row, m.id)!.text) }}</b>
                  <template v-else>{{ summarize(cellOf(row, m.id)!.text) }}</template>
                </template>
                <span v-else class="cell-default">(默认)</span>
              </button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="`编辑格子: ${editCellId}`" width="640px">
      <p class="hint">输入引导文本; 留空保存将恢复为默认。</p>
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
  text-overflow: ellipsis;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-regular);
  text-align: left;
  white-space: nowrap;
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
