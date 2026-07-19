<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPrompt,
  getPromptHistory,
  putPrompt,
  resetPrompt,
  validatePrompt,
} from '@/api/client'
import type { PromptDetail, PromptHistoryItem } from '@/types/api'

const props = defineProps<{ name: string }>()
const router = useRouter()

const detail = ref<PromptDetail | null>(null)
const loading = ref(false)
const saving = ref(false)
const validating = ref(false)
const resetting = ref(false)

const draft = ref('')
const historyOpen = ref(false)
const history = ref<PromptHistoryItem[]>([])
const historyLoading = ref(false)

const dirty = computed(() => !!detail.value && draft.value !== detail.value.current)

async function load() {
  loading.value = true
  try {
    detail.value = await getPrompt(props.name)
    draft.value = detail.value.current
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onValidate(silentOk = false): Promise<boolean> {
  if (!detail.value) return false
  validating.value = true
  try {
    const r = await validatePrompt(detail.value.name, draft.value)
    if (r.ok) {
      if (!silentOk) ElMessage.success('校验通过')
      return true
    }
    if (r.missing_placeholders.length) {
      ElMessage.error(`缺少占位符: ${r.missing_placeholders.join(', ')}`)
    } else {
      ElMessage.error(r.error ?? '校验失败')
    }
    return false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    return false
  } finally {
    validating.value = false
  }
}

async function onSave() {
  if (!detail.value) return
  const ok = await onValidate(true)
  if (!ok) return

  saving.value = true
  try {
    const summary = await putPrompt(detail.value.name, draft.value)
    ElMessage.success(`已保存 (版本 ${summary.version})`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function onReset() {
  if (!detail.value?.overridden) {
    ElMessage.info('未覆盖, 无需重置')
    return
  }
  try {
    await ElMessageBox.confirm(
      '重置后, 当前覆盖将被移动到历史备份, 提示词恢复为默认版本。确认继续？',
      '重置为默认',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    await resetPrompt(detail.value.name)
    ElMessage.success('已重置为默认版本')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    resetting.value = false
  }
}

async function openHistory() {
  if (!detail.value) return
  historyOpen.value = true
  historyLoading.value = true
  try {
    const r = await getPromptHistory(detail.value.name)
    history.value = r.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    historyLoading.value = false
  }
}

async function onBack() {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('有未保存的修改, 确认放弃并返回？', '未保存', {
        type: 'warning',
        confirmButtonText: '放弃',
        cancelButtonText: '继续编辑',
      })
    } catch {
      return
    }
  }
  router.push('/prompts')
}

watch(
  () => props.name,
  () => {
    if (props.name) load()
  },
)

onMounted(load)
</script>

<template>
  <div class="page-container prompt-edit">
    <div class="page-head">
      <div class="head-left">
        <el-button link @click="onBack">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回</span>
        </el-button>
        <div>
          <h2 class="page-title mono">{{ name }}</h2>
          <p v-if="detail" class="page-subtitle">
            {{ detail.description }}
          </p>
        </div>
      </div>
      <div class="head-right">
        <el-tag v-if="detail?.overridden" type="warning" size="small">已覆盖</el-tag>
        <el-tag v-else-if="detail" type="success" size="small">默认</el-tag>
        <el-tag v-if="detail" size="small">v{{ detail.version }}</el-tag>
      </div>
    </div>

    <div v-if="detail && detail.placeholders.length" class="placeholders">
      <span class="ph-label">占位符：</span>
      <el-tag
        v-for="ph in detail.placeholders"
        :key="ph"
        size="small"
        type="info"
        class="mono"
      >
        {{ ph }}
      </el-tag>
    </div>

    <div v-loading="loading" class="editor-grid">
      <el-card shadow="never" class="pane">
        <template #header>
          <div class="pane-head">
            <span>当前版本 (可编辑)</span>
            <el-tag v-if="dirty" type="warning" size="small">未保存</el-tag>
          </div>
        </template>
        <el-input
          v-model="draft"
          type="textarea"
          :rows="26"
          resize="none"
          class="mono"
          spellcheck="false"
          placeholder="# 提示词内容 (Markdown, 可含 YAML frontmatter)"
        />
      </el-card>

      <el-card shadow="never" class="pane">
        <template #header>
          <div class="pane-head">
            <span>默认版本 (只读)</span>
          </div>
        </template>
        <el-input
          :model-value="detail?.default ?? ''"
          type="textarea"
          :rows="26"
          resize="none"
          class="mono"
          readonly
        />
      </el-card>
    </div>

    <div class="actions">
      <el-button :loading="validating" @click="onValidate(false)">
        <el-icon><Check /></el-icon>
        <span>校验</span>
      </el-button>
      <el-button @click="openHistory">
        <el-icon><Clock /></el-icon>
        <span>历史备份</span>
      </el-button>
      <el-button
        type="danger"
        plain
        :loading="resetting"
        :disabled="!detail?.overridden"
        @click="onReset"
      >
        重置为默认
      </el-button>
      <el-button type="primary" :loading="saving" :disabled="!dirty" @click="onSave">
        保存
      </el-button>
    </div>

    <el-drawer v-model="historyOpen" title="历史备份" size="480px">
      <div v-loading="historyLoading">
        <el-empty v-if="!historyLoading && !history.length" description="暂无历史备份" />
        <el-table v-else :data="history" size="small" stripe>
          <el-table-column prop="filename" label="文件" min-width="200">
            <template #default="{ row }">
              <span class="mono">{{ row.filename }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="mtime" label="时间" width="180">
            <template #default="{ row }">
              <span class="mono">{{ row.mtime }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="size" label="大小" width="100" align="right">
            <template #default="{ row }">{{ row.size }} B</template>
          </el-table-column>
        </el-table>
        <p class="drawer-hint">
          备份最多保留 10 份, 存于服务器
          <span class="mono">data/prompts/.history/</span>。
        </p>
      </div>
    </el-drawer>
  </div>
</template>

<style lang="scss" scoped>
.prompt-edit {
  padding-bottom: $space-8;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
}

.head-left {
  display: flex;
  align-items: center;
  gap: $space-3;
}

.head-right {
  display: flex;
  gap: $space-2;
}

.placeholders {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;
  margin-bottom: $space-4;
  padding: $space-2 $space-3;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-sm;
}

.ph-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.editor-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;
  margin-bottom: $space-4;

  @include respond-to(lg) {
    grid-template-columns: 1fr 1fr;
  }
}

.pane :deep(.el-textarea__inner) {
  font-family: 'JetBrains Mono', Menlo, Monaco, Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.pane-head {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: $space-2;
}

.drawer-hint {
  margin-top: $space-3;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
