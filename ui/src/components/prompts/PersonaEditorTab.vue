<script setup lang="ts">
import { ref, watch, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPersonaDefinition,
  savePersonaDefinition,
  listPersonaVersions,
  rollbackPersonaVersion,
  listPersonaProfiles,
  createPersonaProfile,
  activatePersonaProfile,
  deletePersonaProfile,
  importCharacterCard,
  exportPersona,
} from '@/api/client'
import type {
  CharacterCardPreview,
  PersonaDefinitionRead,
  PersonaProfileRead,
  PersonaVersionItem,
} from '@/types/api'

const props = defineProps<{
  active: boolean
}>()

const loading = ref(false)
const saving = ref(false)
const definition = ref<PersonaDefinitionRead | null>(null)
const versions = ref<PersonaVersionItem[]>([])
const versionsLoading = ref(false)
const versionDialogVisible = ref(false)

const profiles = ref<PersonaProfileRead[]>([])
const profilesLoading = ref(false)
const activeProfileId = ref<string | null>(null)

const form = reactive({
  name: '',
  personality: '',
  speaking_style: '',
  values: [] as string[],
  persona_addressing: '人格',
  changelog: '',
})

const valuesText = ref('')
const spaceOverrideDialogVisible = ref(false)
const spaceOverrides = ref<Record<string, { speaking_style: string; personality: string; scenario: string }>>({})
const editSpaceId = ref('')
const editOverridePersonality = ref('')
const editOverrideSpeakingStyle = ref('')
const editOverrideScenario = ref('')

const newProfileDialogVisible = ref(false)
const newProfileName = ref('')
const newProfileDesc = ref('')

const fileInputRef = ref<HTMLInputElement | null>(null)
const importing = ref(false)
const importDialogVisible = ref(false)
const importPreview = ref<CharacterCardPreview | null>(null)
const importSaving = ref(false)

function hydrate(d: PersonaDefinitionRead) {
  definition.value = d
  form.name = d.name
  form.personality = d.identity.personality
  form.speaking_style = d.identity.speaking_style
  form.values = d.identity.values
  valuesText.value = d.identity.values.join('\n')
  form.persona_addressing = d.identity.persona_addressing
  spaceOverrides.value = {}
  for (const [sid, ov] of Object.entries(d.space_overrides)) {
    spaceOverrides.value[sid] = {
      speaking_style: ov.speaking_style || '',
      personality: ov.personality || '',
      scenario: ov.scenario || '',
    }
  }
}

async function loadProfiles() {
  profilesLoading.value = true
  try {
    const res = await listPersonaProfiles()
    profiles.value = res.items
    const active = res.items.find(p => p.is_active)
    activeProfileId.value = active?.id || null
  } catch {
    // 首次使用时 profiles 表可能还不存在, 静默处理
    profiles.value = []
  } finally {
    profilesLoading.value = false
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

async function loadAll() {
  await loadProfiles()
  await load()
}

async function onSwitchProfile(pid: string) {
  if (pid === activeProfileId.value) return
  try {
    await activatePersonaProfile(pid)
    ElMessage.success('已切换人格')
    await loadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onCreateProfile() {
  if (!newProfileName.value.trim()) {
    ElMessage.warning('人格名称不能为空')
    return
  }
  try {
    await createPersonaProfile({
      name: newProfileName.value.trim(),
      description: newProfileDesc.value.trim(),
    })
    ElMessage.success('已创建新人格')
    newProfileDialogVisible.value = false
    newProfileName.value = ''
    newProfileDesc.value = ''
    await loadProfiles()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onDeleteProfile(pid: string) {
  const profile = profiles.value.find(p => p.id === pid)
  if (!profile) return
  try {
    await ElMessageBox.confirm(
      `确认删除人格「${profile.name}」及其所有版本？此操作不可撤销。`,
      '删除人格',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deletePersonaProfile(pid)
    ElMessage.success(`已删除「${profile.name}」`)
    await loadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function onPickImportFile() {
  fileInputRef.value?.click()
}

async function onImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  try {
    importPreview.value = await importCharacterCard(file)
    importDialogVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    importing.value = false
  }
}

async function onConfirmImport() {
  const preview = importPreview.value
  if (!preview) return
  importSaving.value = true
  try {
    const parsedValues = (preview.identity.values || []).filter(v => v.trim())
    hydrate(await savePersonaDefinition({
      name: preview.name || form.name || '导入人格',
      identity: {
        personality: preview.identity.personality,
        speaking_style: preview.identity.speaking_style,
        values: parsedValues,
        persona_addressing: preview.identity.persona_addressing || '角色',
      },
      space_overrides: {},
      changelog: `导入角色卡 (${preview.source_format})`,
    }))
    form.changelog = ''
    importDialogVisible.value = false
    importPreview.value = null
    ElMessage.success(`已导入角色卡「${preview.name}」`)
    await loadProfiles()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    importSaving.value = false
  }
}

async function onExport() {
  try {
    await exportPersona()
    ElMessage.success('人格已导出')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onSave() {
  if (!form.name.trim()) {
    ElMessage.warning('人格名称不能为空')
    return
  }
  saving.value = true
  try {
    const parsedValues = valuesText.value
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)

    const overrides: Record<string, { speaking_style: string | null; personality: string | null; scenario: string | null }> = {}
    for (const [sid, ov] of Object.entries(spaceOverrides.value)) {
      overrides[sid] = {
        speaking_style: ov.speaking_style || null,
        personality: ov.personality || null,
        scenario: ov.scenario || null,
      }
    }

    hydrate(await savePersonaDefinition({
      name: form.name.trim(),
      identity: {
        personality: form.personality,
        speaking_style: form.speaking_style,
        values: parsedValues,
        persona_addressing: form.persona_addressing,
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
      `确认回滚到版本 ${v.version}？`,
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
  editOverrideScenario.value = ''
  spaceOverrideDialogVisible.value = true
}

function editSpaceOverride(sid: string) {
  const ov = spaceOverrides.value[sid]
  if (!ov) return
  editSpaceId.value = sid
  editOverridePersonality.value = ov.personality
  editOverrideSpeakingStyle.value = ov.speaking_style
  editOverrideScenario.value = ov.scenario
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
    scenario: editOverrideScenario.value,
  }
  spaceOverrideDialogVisible.value = false
}

function removeSpaceOverride(sid: string) {
  delete spaceOverrides.value[sid]
}

watch(
  () => props.active,
  (active) => {
    if (active && !definition.value) loadAll()
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
        <el-button size="small" @click="newProfileDialogVisible = true">新建人格</el-button>
        <el-button size="small" :loading="importing" @click="onPickImportFile">导入角色卡</el-button>
        <el-button size="small" @click="onExport">导出</el-button>
        <el-button size="small" @click="openVersions">版本历史</el-button>
      </div>
    </div>

    <input
      ref="fileInputRef"
      type="file"
      accept=".png,.json,image/png,application/json"
      style="display: none"
      @change="onImportFileChange"
    />

    <el-card v-loading="loading || profilesLoading">
      <!-- 人格选择器 -->
      <div v-if="profiles.length > 1" class="profile-selector">
        <span class="profile-label">当前人格：</span>
        <el-select
          :model-value="activeProfileId"
          @update:model-value="onSwitchProfile"
          style="width: 240px"
        >
          <el-option
            v-for="p in profiles"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          >
            <span>{{ p.name }}</span>
            <el-tag v-if="p.is_active" size="small" type="success" style="margin-left: 8px">当前</el-tag>
          </el-option>
        </el-select>
        <el-button
          v-if="activeProfileId"
          size="small"
          text
          type="danger"
          @click="onDeleteProfile(activeProfileId)"
        >
          删除当前
        </el-button>
      </div>

      <el-form label-width="140px" class="persona-form">
        <el-form-item label="人格名称">
          <el-input v-model="form.name" placeholder="输入人格名称" />
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

        <el-form-item label="人格自称">
          <el-input v-model="form.persona_addressing" placeholder="我" />
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
              {{ ov.scenario ? '场景 ✓' : '' }}
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
        <el-form-item label="覆盖场景">
          <el-input v-model="editOverrideScenario" placeholder="在该空间覆盖默认场景描述" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveSpaceOverride">确定</el-button>
          <el-button @click="spaceOverrideDialogVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- 新建人格对话框 -->
    <el-dialog
      v-model="newProfileDialogVisible"
      title="新建人格"
      width="400px"
    >
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="newProfileName" placeholder="输入人格名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="newProfileDesc" type="textarea" :rows="2" placeholder="可选描述" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onCreateProfile">创建</el-button>
          <el-button @click="newProfileDialogVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- 导入角色卡预览对话框 -->
    <el-dialog
      v-model="importDialogVisible"
      :title="`导入角色卡: ${importPreview?.name || ''}`"
      width="560px"
    >
      <div v-if="importPreview" class="import-preview">
        <div class="import-meta">
          <el-tag size="small">{{ importPreview.source_format }}</el-tag>
          <el-tag v-if="importPreview.has_lorebook" size="small" type="warning">含世界书</el-tag>
          <el-tag v-if="importPreview.has_examples" size="small" type="info">含示例对话</el-tag>
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="人格设定">
            <pre class="mono preview-text">{{ importPreview.identity.personality || '—' }}</pre>
          </el-descriptions-item>
          <el-descriptions-item label="说话风格">
            <pre class="mono preview-text">{{ importPreview.identity.speaking_style || '—' }}</pre>
          </el-descriptions-item>
          <el-descriptions-item label="核心价值">
            <div v-if="importPreview.identity.values.length">
              <el-tag v-for="v in importPreview.identity.values" :key="v" size="small" style="margin-right: 6px">
                {{ v }}
              </el-tag>
            </div>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="人格自称">
            {{ importPreview.identity.persona_addressing || '角色' }}
          </el-descriptions-item>
        </el-descriptions>
        <p class="import-hint">确认后将以新版本保存当前激活人格。</p>
      </div>
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="importSaving" @click="onConfirmImport">确认导入</el-button>
      </template>
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

.profile-selector {
  display: flex;
  align-items: center;
  gap: $space-3;
  margin-bottom: $space-4;
  padding-bottom: $space-3;
  border-bottom: 1px solid var(--el-border-color-lighter);

  .profile-label {
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
  }
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

.import-preview {
  margin-bottom: $space-2;

  .import-meta {
    display: flex;
    gap: $space-2;
    margin-bottom: $space-3;
  }

  .preview-text {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 13px;
  }

  .import-hint {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin: $space-3 0 0;
  }
}
</style>
