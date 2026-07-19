<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPersonaConfig,
  listPrompts,
  resetPersonaConfig,
  resetPrompt,
  updatePersonaConfig,
} from '@/api/client'
import type { PersonaConfigRead, PromptSummary } from '@/types/api'

const router = useRouter()

// ---- 提示词列表 ----
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
  router.push({ name: 'prompt-edit', params: { name: row.name } })
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

// ---- 人格编辑 ----
const activeTab = ref('prompts')
const personaLoading = ref(false)
const persona = ref<PersonaConfigRead | null>(null)
const editName = ref('')
const editPrompt = ref('')
const editAddr = ref('')
const editUserAddr = ref('')
const editContext = ref('')

async function loadPersona() {
  personaLoading.value = true
  try {
    persona.value = await getPersonaConfig()
    editName.value = persona.value.name
    editPrompt.value = persona.value.prompt
    editAddr.value = persona.value.relation.persona_addressing
    editUserAddr.value = persona.value.relation.user_addressing
    editContext.value = persona.value.relation.context
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    personaLoading.value = false
  }
}

function onTabChange(tab: string) {
  if (tab === 'persona' && !persona.value) {
    loadPersona()
  }
}

async function onSavePersona() {
  if (!editName.value.trim()) {
    ElMessage.warning('人格名称不能为空')
    return
  }
  if (!editPrompt.value.trim()) {
    ElMessage.warning('人格提示词不能为空')
    return
  }
  personaLoading.value = true
  try {
    persona.value = await updatePersonaConfig({
      name: editName.value.trim(),
      prompt: editPrompt.value,
      relation: {
        persona_addressing: editAddr.value.trim(),
        user_addressing: editUserAddr.value.trim(),
        context: editContext.value.trim(),
      },
    })
    // sync local edits after save
    editName.value = persona.value.name
    editPrompt.value = persona.value.prompt
    editAddr.value = persona.value.relation.persona_addressing
    editUserAddr.value = persona.value.relation.user_addressing
    editContext.value = persona.value.relation.context
    ElMessage.success('人格已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    personaLoading.value = false
  }
}

async function onResetPersona() {
  if (!persona.value?.overridden) {
    ElMessage.info('人格未覆盖, 无需重置')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确认重置人格为默认？当前覆盖将被删除, 回退到 config.local.toml / 资源默认值。',
      '重置人格',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  personaLoading.value = true
  try {
    persona.value = await resetPersonaConfig()
    editName.value = persona.value.name
    editPrompt.value = persona.value.prompt
    editAddr.value = persona.value.relation.persona_addressing
    editUserAddr.value = persona.value.relation.user_addressing
    editContext.value = persona.value.relation.context
    ElMessage.success('人格已重置为默认')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    personaLoading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="提示词" name="prompts">
        <div class="page-head">
          <div>
            <h2 class="page-title">提示词管理</h2>
            <p class="page-subtitle">
              管理系统内建的 Agent 提示词。修改后的版本存于 <span class="mono">data/prompts/</span>，
              随时可重置为默认。
            </p>
          </div>
          <div class="head-actions">
            <el-input
              v-model="query"
              placeholder="搜索名称或描述"
              clearable
              class="search"
            >
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
            <el-table-column prop="description" label="描述" min-width="260" show-overflow-tooltip />
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
      </el-tab-pane>

      <el-tab-pane label="人格编辑" name="persona">
        <div class="page-head">
          <div>
            <h2 class="page-title">人格编辑</h2>
            <p class="page-subtitle">
              编辑服务器人格。修改将写入 <span class="mono">data/persona_override.toml</span>，
              运行时热重载。
            </p>
          </div>
          <div class="head-actions">
            <el-tag v-if="persona?.overridden" type="warning" size="small">已覆盖</el-tag>
            <el-tag v-else type="success" size="small">默认</el-tag>
          </div>
        </div>

        <el-card v-loading="personaLoading" shadow="never">
          <el-form label-width="140px" class="persona-form">
            <el-form-item label="人格名称">
              <el-input v-model="editName" placeholder="绫音" />
            </el-form-item>

            <el-form-item label="人格提示词">
              <el-input
                v-model="editPrompt"
                type="textarea"
                :rows="12"
                placeholder="完整人格提示词..."
                class="mono"
              />
            </el-form-item>

            <el-divider content-position="left">关系框架</el-divider>

            <el-form-item label="人格自称">
              <el-input v-model="editAddr" placeholder="我" />
            </el-form-item>

            <el-form-item label="人格称呼用户">
              <el-input v-model="editUserAddr" placeholder="哥哥" />
            </el-form-item>

            <el-form-item label="关系背景">
              <el-input
                v-model="editContext"
                placeholder="同住的兄妹, 无血缘关系"
                type="textarea"
                :rows="2"
              />
            </el-form-item>

            <el-form-item>
              <div class="form-actions">
                <el-button type="primary" :loading="personaLoading" @click="onSavePersona">
                  保存
                </el-button>
                <el-button
                  type="danger"
                  :disabled="!persona?.overridden"
                  :loading="personaLoading"
                  @click="onResetPersona"
                >
                  重置为默认
                </el-button>
              </div>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>
    </el-tabs>
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

.persona-form {
  max-width: 800px;
}

.form-actions {
  display: flex;
  gap: $space-2;
}
</style>
