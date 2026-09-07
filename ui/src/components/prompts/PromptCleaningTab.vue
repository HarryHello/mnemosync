<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  clearPromptCleanCache,
  deletePromptCleanCache,
  listPromptCleanCache,
  listPromptCleanSettings,
  reCleanPromptCache,
  setPromptCleanSetting,
  updatePromptCleanCache,
} from '@/api/client'
import type { PromptCacheEntryDto, PromptCleanSettingDto } from '@/api/client'
import { formatDate } from '@/utils/format'
import TabHeader from '@/components/common/TabHeader.vue'

const items = ref<PromptCacheEntryDto[]>([])
const total = ref(0)
const loading = ref(false)
const frontendFilter = ref('')
const page = ref(1)
const pageSize = ref(50)

const settings = ref<PromptCleanSettingDto[]>([])
const settingsFrontend = ref('')

// 下拉候选前台
const frontendOptions = computed(() => {
  const s = new Set<string>([...items.value.map((i) => i.frontend), ...settings.value.map((s) => s.frontend)])
  s.delete('*')
  return [...s]
})

async function refresh() {
  loading.value = true
  try {
    const res = await listPromptCleanCache(frontendFilter.value || undefined, page.value, pageSize.value)
    items.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function refreshSettings() {
  try {
    settings.value = (await listPromptCleanSettings()).items
  } catch {
    settings.value = []
  }
}

const editVisible = ref(false)
const editing = ref<PromptCacheEntryDto | null>(null)
const editText = ref('')

function openEdit(row: PromptCacheEntryDto) {
  editing.value = row
  editText.value = row.clean_prompt
  editVisible.value = true
}

async function onEditSave() {
  if (!editing.value) return
  try {
    await updatePromptCleanCache(editing.value.frontend, editing.value.module_hash, editText.value)
    ElMessage.success('已保存')
    editVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onReClean(row: PromptCacheEntryDto) {
  try {
    await ElMessageBox.confirm(`当场重新清洗模块 [${row.module_title}]?`, '重新清洗', {
      confirmButtonText: '重洗', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  try {
    const res = await reCleanPromptCache(row.frontend, row.module_hash)
    ElMessage.success('清洗完成')
    row.clean_prompt = res.clean_prompt
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onDelete(row: PromptCacheEntryDto) {
  try {
    await ElMessageBox.confirm(`删除模块 [${row.module_title}] 的缓存? 下次请求将自动重新清洗。`, '删除缓存', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  try {
    await deletePromptCleanCache(row.frontend, row.module_hash)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onClearAll() {
  try {
    await ElMessageBox.confirm('清空全部清洗缓存? 所有前台下次请求都会重新清洗。', '清空缓存', {
      confirmButtonText: '清空', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  try {
    const res = await clearPromptCleanCache()
    ElMessage.success(`已清空 ${res.deleted} 条`)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

// ── 跳过配置 ────────────────────────────────────────────────

const skipTitles = computed(() => {
  const f = settingsFrontend.value || '*'
  return new Set(
    settings.value
      .filter((s) => s.frontend === f && s.skip)
      .map((s) => s.module_title),
  )
})

async function toggleSkip(moduleTitle: string, skip: boolean) {
  const f = settingsFrontend.value || '*'
  try {
    await setPromptCleanSetting(f, moduleTitle, skip)
    await refreshSettings()
    ElMessage.success(skip ? `已配置跳过 [${moduleTitle}]` : `已取消跳过 [${moduleTitle}]`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(() => {
  void refresh()
  void refreshSettings()
})
</script>

<template>
  <div>
    <TabHeader
      title="提示词清洗缓存"
      subtitle="客户端 system 提示词按标题拆分为模块, 每个模块的清洗结果按前台缓存。跳过模块=原样保留; 手动编辑直接改缓存值; 删除缓存后下次请求自动重新清洗。"
    >
      <template #actions>
        <el-button :loading="loading" @click="refresh"><el-icon><Refresh /></el-icon><span>刷新</span></el-button>
        <el-button type="danger" plain @click="onClearAll">清空缓存</el-button>
      </template>
    </TabHeader>

    <el-row :gutter="16">
      <el-col :span="16">
        <el-card>
          <div class="filter-bar">
            <el-input v-model="frontendFilter" placeholder="按前台过滤 (api_key.note)" clearable style="width: 260px" @keyup.enter="page = 1; refresh()" />
            <el-button @click="page = 1; refresh()">查询</el-button>
            <span class="muted">共 {{ total }} 条</span>
          </div>
          <el-table :data="items" v-loading="loading" stripe empty-text="暂无缓存">
            <el-table-column label="前台" prop="frontend" min-width="110" show-overflow-tooltip />
            <el-table-column label="模块" prop="module_title" min-width="140" show-overflow-tooltip />
            <el-table-column label="原文摘要" min-width="180">
              <template #default="{ row }: { row: PromptCacheEntryDto }">
                <span class="mono muted text-clip">{{ row.module_text }}</span>
              </template>
            </el-table-column>
            <el-table-column label="清洗结果" min-width="200">
              <template #default="{ row }: { row: PromptCacheEntryDto }">
                <span v-if="row.skipped" class="tag-skip">跳过</span>
                <span v-else class="mono text-clip">{{ row.clean_prompt }}</span>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" min-width="150">
              <template #default="{ row }: { row: PromptCacheEntryDto }">
                <span class="muted">{{ formatDate(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right">
              <template #default="{ row }: { row: PromptCacheEntryDto }">
                <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button link type="warning" @click="onReClean(row)">重新清洗</el-button>
                <el-button link type="danger" @click="onDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next"
            class="pagination"
            @current-change="refresh"
          />
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card>
          <template #header><span>跳过不清洗配置</span></template>
          <el-form label-width="70px" label-position="top">
            <el-form-item label="前台">
              <el-select v-model="settingsFrontend" clearable placeholder="全局 (*)">
                <el-option v-for="f in frontendOptions" :key="f" :value="f" :label="f" />
              </el-select>
            </el-form-item>
          </el-form>
          <p class="hint">
            按前台配置「不清洗」的模块: 该前台的这些模块将原样保留, 不调 LLM 清洗。
            不选前台时配置的是全局 (*), 对所有前台生效。
          </p>
          <div v-if="frontendOptions.length || settings.length" class="skip-list">
            <div v-for="t in [...skipTitles]" :key="t" class="skip-row">
              <span class="mono">{{ t }}</span>
              <el-button link type="danger" @click="toggleSkip(t, false)">取消跳过</el-button>
            </div>
            <el-empty v-if="skipTitles.size === 0" description="暂无跳过模块" :image-size="50" />
          </div>
          <el-empty v-else description="暂无缓存模块可配置" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" title="编辑清洗结果" width="640px">
      <el-form label-width="90px">
        <el-form-item label="前台">
          <span class="mono">{{ editing?.frontend }}</span>
        </el-form-item>
        <el-form-item label="模块">
          <span class="mono">{{ editing?.module_title }}</span>
        </el-form-item>
        <el-form-item label="清洗结果">
          <el-input v-model="editText" type="textarea" :rows="10" placeholder="清空 = 全部丢弃" />
        </el-form-item>
        <el-form-item label="原文">
          <pre class="raw-box mono">{{ editing?.module_text }}</pre>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="onEditSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

.text-clip {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
  white-space: nowrap;
}

.tag-skip {
  font-size: 12px;
  color: var(--el-color-warning);
}

.pagination {
  justify-content: flex-end;
  margin-top: 12px;
}

.hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.skip-list {
  max-height: 360px;
  overflow: auto;
}

.skip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.raw-box {
  max-height: 160px;
  padding: 8px;
  margin: 0;
  overflow: auto;
  font-size: 11px;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
</style>