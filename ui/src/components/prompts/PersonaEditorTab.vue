<script setup lang="ts">
import { ref, watch, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPersonaDefinition,
  savePersonaDefinition,
  listPersonaVersions,
  rollbackPersonaVersion,
} from '@/api/client'
import type { PersonaDefinitionRead, PersonaVersionItem } from '@/types/api'

const props = defineProps<{
  active: boolean
}>()

const loading = ref(false)
const saving = ref(false)
const definition = ref<PersonaDefinitionRead | null>(null)
const versions = ref<PersonaVersionItem[]>([])
const versionsLoading = ref(false)
const versionDialogVisible = ref(false)

const form = reactive({
  personality: '',
  speaking_style: '',
  values: [] as string[],
  persona_addressing: '人格',
  user_addressing: '用户',
  context: '',
  changelog: '',
})

const valuesText = ref('')
const spaceOverrideDialogVisible = ref(false)
const spaceOverrides = ref<Record<string, { speaking_style: string; personality: string; context: string }>>({})
const editSpaceId = ref('')
const editOverridePersonality = ref('')
const editOverrideSpeakingStyle = ref('')
const editOverrideContext = ref('')

function hydrate(d: PersonaDefinitionRead) {
  definition.value = d
  form.personality = d.identity.personality
  form.speaking_style = d.identity.speaking_style
  form.values = d.identity.values
  valuesText.value = d.identity.values.join('\n')
  form.persona_addressing = d.identity.persona_addressing
  form.user_addressing = d.identity.user_addressing
  form.context = d.identity.context
  spaceOverrides.value = {}
  for (const [sid, ov] of Object.entries(d.space_overrides)) {
    spaceOverrides.value[sid] = {
      speaking_style: ov.speaking_style || '',
      personality: ov.personality || '',
      context: ov.context || '',
    }
  }
}

async function load() {
  loading.value = true
  try {
    hydrate(await getPersonaDefinition())
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!definition.value?.name) {
    ElMessage.warning('人格名称不能为空')
    return
  }
  saving.value = true
  try {
    const parsedValues = valuesText.value
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)

    const overrides: Record<string, { speaking_style: string | null; personality: string | null; context: string | null }> = {}
    for (const [sid, ov] of Object.entries(spaceOverrides.value)) {
      overrides[sid] = {
        speaking_style: ov.speaking_style || null,
        personality: ov.personality || null,
        context: ov.context || null,
      }
    }

    hydrate(await savePersonaDefinition({
      identity: {
        personality: form.personality,
        speaking_style: form.speaking_style,
        values: parsedValues,
        persona_addressing: form.persona_addressing,
        user_addressing: form.user_addressing,
        context: form.context,
      },
      space_overrides: overrides,
      changelog: form.changelog,
    }))
    form.changelog = ''
    ElMessage.success('人格已保存 (新版本)')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function loadVersions() {
  versionsLoading.value = true
  try {
    const res = await listPersonaVersions()
    versions.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    versionsLoading.value = false
  }
}

function openVersions() {
  versionDialogVisible.value = true
  loadVersions()
}

async function onRollback(v: PersonaVersionItem) {
  if (v.active) {
    ElMessage.info('当前已是该版本')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认回滚到版本 ${v.version}？这将是第 ${versions.value.length} 次版本变更。`,
      '回滚人格',
      { type: 'warning', confirmButtonText: '回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  versionsLoading.value = true
  try {
    await rollbackPersonaVersion(v.id)
    ElMessage.success(`已回滚到版本 ${v.version}`)
    versionDialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    versionsLoading.value = false
  }
}

function openAddSpaceOverride() {
  editSpaceId.value = ''
  editOverridePersonality.value = ''
  editOverrideSpeakingStyle.value = ''
  editOverrideContext.value = ''
  spaceOverrideDialogVisible.value = true
}

function editSpaceOverride(sid: string) {
  const ov = spaceOverrides.value[sid]
  if (!ov) return
  editSpaceId.value = sid
  editOverridePersonality.value = ov.personality
  editOverrideSpeakingStyle.value = ov.speaking_style
  editOverrideContext.value = ov.context
  spaceOverrideDialogVisible.value = true
}

function saveSpaceOverride() {
  if (!editSpaceId.value.trim()) {
    ElMessage.warning('空间 ID 不能为空')
    return
  }
  spaceOverrides.value[editSpaceId.value.trim()] = {
    speaking_style: editOverrideSpeakingStyle.value,
    personality: editOverridePersonality.value,
    context: editOverrideContext.value,
  }
  spaceOverrideDialogVisible.value = false
}

function removeSpaceOverride(sid: string) {
  delete spaceOverrides.value[sid]
}

watch(
  () => props.active,
  (active) => {
    if (active && !definition.value) load()
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">人格编辑</h3>
        <p class="tab-subtitle">
          编辑结构化人格。保存将创建新版本，
          当前版本 {{ definition?.version || '—' }}
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" @click="openVersions">版本历史</el-button>
      </div>
    </div>

    <el-card v-loading="loading">
      <el-form label-width="140px" class="persona-form">
        <el-form-item label="人格名称">
          <el-input :model-value="definition?.name" disabled placeholder="名称来自配置" />
        </el-form-item>

        <el-form-item label="人格设定">
          <el-input
            v-model="form.personality"
            type="textarea"
            :rows="8"
            placeholder="你是..."
            class="mono"
          />
        </el-form-item>

        <el-form-item label="说话风格">
          <el-input
            v-model="form.speaking_style"
            type="textarea"
            :rows="3"
            placeholder="冷淡、短句、少用语气词..."
          />
        </el-form-item>

        <el-form-item label="核心价值">
          <el-input
            v-model="valuesText"
            type="textarea"
            :rows="3"
            placeholder="一行一个值&#10;重视家人&#10;不喜欢说谎"
          />
        </el-form-item>

        <el-divider content-position="left">关系框架</el-divider>

        <el-form-item label="人格自称">
          <el-input v-model="form.persona_addressing" placeholder="我" />
        </el-form-item>

        <el-form-item label="人格称呼用户">
          <el-input v-model="form.user_addressing" placeholder="哥哥" />
        </el-form-item>

        <el-form-item label="关系背景">
          <el-input
            v-model="form.context"
            placeholder="同住的兄妹"
            type="textarea"
            :rows="2"
          />
        </el-form-item>

        <el-divider content-position="left">空间覆盖（可选）</el-divider>

        <div class="space-overrides">
          <div
            v-for="(ov, sid) in spaceOverrides"
            :key="sid"
            class="space-override-item"
          >
            <code>{{ sid }}</code>
            <span class="override-summary">
              {{ ov.speaking_style ? '说话风格 ✓' : '' }}
              {{ ov.personality ? '人格设定 ✓' : '' }}
              {{ ov.context ? '背景 ✓' : '' }}
            </span>
            <el-button size="small" text @click="editSpaceOverride(sid)">编辑</el-button>
            <el-button size="small" text type="danger" @click="removeSpaceOverride(sid)">删除</el-button>
          </div>
          <div v-if="!Object.keys(spaceOverrides).length" class="no-overrides">
            尚无空间覆盖。添加后可在特定群聊中覆盖人格设定。
          </div>
          <el-button size="small" @click="openAddSpaceOverride">添加空间覆盖</el-button>
        </div>

        <el-divider content-position="left">保存</el-divider>

        <el-form-item label="变更说明">
          <el-input
            v-model="form.changelog"
            placeholder="如：修正背景设定错误"
          />
        </el-form-item>

        <el-form-item>
          <div class="form-actions">
            <el-button type="primary" :loading="saving" @click="onSave">保存 (创建新版本)</el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 版本历史对话框 -->
    <el-dialog
      v-model="versionDialogVisible"
      title="版本历史"
      width="600px"
    >
      <div v-loading="versionsLoading">
        <div v-for="v in versions" :key="v.id" class="version-item">
          <div class="version-head">
            <el-tag v-if="v.active" type="success" size="small">当前</el-tag>
            <code>v{{ v.version }}</code>
            <span class="version-date">{{ new Date(v.created_at).toLocaleString('zh-CN', { hour12: false }) }}</span>
            <span v-if="v.author" class="version-author">by {{ v.author }}</span>
          </div>
          <div v-if="v.changelog" class="version-changelog">{{ v.changelog }}</div>
          <el-button
            v-if="!v.active"
            size="small"
            text
            type="primary"
            @click="onRollback(v)"
          >
            回滚到此版本
          </el-button>
        </div>
        <div v-if="!versions.length" class="empty">暂无版本记录</div>
      </div>
    </el-dialog>

    <!-- 空间覆盖编辑对话框 -->
    <el-dialog
      v-model="spaceOverrideDialogVisible"
      :title="editSpaceId ? `编辑空间覆盖: ${editSpaceId}` : '添加空间覆盖'"
      width="500px"
    >
      <el-form label-width="100px">
        <el-form-item label="空间 ID">
          <el-input v-model="editSpaceId" :disabled="!!editSpaceId" placeholder="space-xxx" class="mono" />
        </el-form-item>
        <el-form-item label="覆盖说话风格">
          <el-input v-model="editOverrideSpeakingStyle" placeholder="在该空间覆盖默认说话风格" />
        </el-form-item>
        <el-form-item label="覆盖人格设定">
          <el-input v-model="editOverridePersonality" type="textarea" :rows="3" placeholder="在该空间覆盖默认人格" />
        </el-form-item>
        <el-form-item label="覆盖背景">
          <el-input v-model="editOverrideContext" placeholder="在该空间覆盖默认背景" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveSpaceOverride">确定</el-button>
          <el-button @click="spaceOverrideDialogVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>
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

.persona-form {
  max-width: 800px;
}

.form-actions {
  display: flex;
  gap: $space-2;
}

.space-overrides {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  margin-bottom: $space-3;
}

.space-override-item {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-sm;
  font-size: 13px;

  code {
    font-family: 'JetBrains Mono', Menlo, monospace;
    color: var(--el-color-primary);
    min-width: 120px;
  }

  .override-summary {
    flex: 1;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
}

.no-overrides {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  padding: $space-2;
}

.version-item {
  display: flex;
  flex-direction: column;
  gap: $space-1;
  padding: $space-3;
  border-bottom: 1px solid var(--el-border-color-lighter);

  .version-head {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: 13px;
  }

  .version-date {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }

  .version-author {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }

  .version-changelog {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-left: $space-4;
  }
}

.empty {
  color: var(--el-text-color-secondary);
  padding: $space-4;
  text-align: center;
}
</style>
