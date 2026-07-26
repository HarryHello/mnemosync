<script setup lang="ts">
/**
 * 身份识别策略管理 (v0.3.0).
 *
 * 一个 API Key 绑定一个策略, 策略定义如何从请求中提取参与者身份。
 * config 为 JSON 字符串, 按策略类型提供模板骨架, 提交前做 JSON 校验。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  createIdentityStrategy,
  deleteIdentityStrategy,
  generateStrategyConfig,
  listIdentityStrategies,
  updateIdentityStrategy,
} from '@/api/client'
import type { IdentityStrategy, IdentityStrategyType } from '@/types/api'

const STRATEGY_TYPES: Array<{ value: IdentityStrategyType; label: string; hint: string }> = [
  {
    value: 'direct',
    label: 'direct — 客户端 user 字段',
    hint: '客户端正确使用 OpenAI request.user 字段 (Web / SDK)',
  },
  {
    value: 'api_key_bound',
    label: 'api_key_bound — Key 即身份',
    hint: 'ChatBox 等单用户本地应用, 固定身份',
  },
  {
    value: 'regex',
    label: 'regex — 正则提取',
    hint: 'AstrBot 等把 QQ号/用户名/群号 塞进 prompt 文本的前台',
  },
  {
    value: 'llm',
    label: 'llm — 辅助模型提取',
    hint: '身份格式不固定, 需要语义理解的前台',
  },
]

const CONFIG_TEMPLATES: Record<IdentityStrategyType, string> = {
  direct: JSON.stringify({ frontend: 'web' }, null, 2),
  api_key_bound: JSON.stringify(
    {
      external_key: 'local-user',
      frontend: 'chatbox',
      display_name: '本地用户',
      channel_type: 'direct',
    },
    null,
    2,
  ),
  regex: JSON.stringify(
    {
      frontend: 'astrbot',
      actor_pattern: 'QQ号[:：]\\s*(\\d+)',
      name_pattern: '用户名[:：]\\s*(\\S+)',
      space_pattern: '群号[:：]\\s*(\\d+)',
      event_id_pattern: '消息ID[:：]\\s*(\\S+)',
      search_in: 'system_or_first_user',
    },
    null,
    2,
  ),
  llm: JSON.stringify(
    {
      frontend: 'custom-bot',
      prompt_template:
        '从以下对话中识别发言者身份。返回 JSON：{"actor_id":"...","actor_name":"...","space_id":"...","event_id":"..."}\n\n{content}',
    },
    null,
    2,
  ),
}

const TYPE_TAG: Record<IdentityStrategyType, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  direct: '',
  api_key_bound: 'success',
  regex: 'warning',
  llm: 'info',
}

const items = ref<IdentityStrategy[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance | null>(null)
const editingId = ref<string | null>(null)

const form = reactive({
  name: '',
  strategy_type: 'regex' as IdentityStrategyType,
  config: CONFIG_TEMPLATES.regex,
})

const rules: FormRules = {
  name: [{ required: true, message: '请填写策略名称', trigger: 'blur' }],
  strategy_type: [{ required: true, message: '请选择策略类型', trigger: 'change' }],
  config: [
    {
      validator: (_rule, value: string, callback) => {
        if (!value || !value.trim()) {
          callback()
          return
        }
        try {
          const parsed = JSON.parse(value)
          if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
            callback(new Error('config 必须是 JSON 对象'))
            return
          }
          callback()
        } catch {
          callback(new Error('config 不是合法 JSON'))
        }
      },
      trigger: 'blur',
    },
  ],
}

const dialogTitle = computed(() => (dialogMode.value === 'create' ? '创建身份策略' : '编辑身份策略'))

// ─── AI 辅助生成 ────────────────────────────────

const aiGenerating = ref(false)
const aiDescription = ref('')
const aiSampleMessage = ref('')
const aiError = ref<string | null>(null)

async function aiGenerate() {
  const desc = aiDescription.value.trim()
  if (desc.length < 10) {
    aiError.value = '请至少输入 10 字的描述, 说明身份信息在消息中的位置和格式'
    return
  }
  aiGenerating.value = true
  aiError.value = null
  try {
    const resp = await generateStrategyConfig({
      strategy_type: form.strategy_type,
      description: desc,
      sample_message: aiSampleMessage.value.trim() || null,
    })
    form.config = resp.config
    ElMessage.success('配置已生成, 请检查并调整后保存')
  } catch (err) {
    aiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    aiGenerating.value = false
  }
}

function resetAiFields() {
  aiDescription.value = ''
  aiSampleMessage.value = ''
  aiError.value = null
}

async function refresh() {
  loading.value = true
  try {
    const res = await listIdentityStrategies()
    items.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  editingId.value = null
  form.name = ''
  form.strategy_type = 'regex'
  form.config = CONFIG_TEMPLATES.regex
  resetAiFields()
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row: IdentityStrategy) {
  dialogMode.value = 'edit'
  editingId.value = row.id
  form.name = row.name
  form.strategy_type = row.strategy_type
  form.config = prettyConfig(row.config)
  resetAiFields()
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function prettyConfig(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function onTypeChange(value: IdentityStrategyType) {
  // 切换类型时: 当前 config 还是旧模板 (未手动改过) → 换新模板; 否则保留手改内容
  const oldTemplates = Object.values(CONFIG_TEMPLATES)
  if (oldTemplates.includes(form.config.trim())) {
    form.config = CONFIG_TEMPLATES[value]
  }
}

function useTemplate() {
  form.config = CONFIG_TEMPLATES[form.strategy_type]
}

async function submit() {
  if (!formRef.value || submitting.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const config = form.config.trim() ? form.config.trim() : '{}'
    if (dialogMode.value === 'create') {
      await createIdentityStrategy({
        name: form.name,
        strategy_type: form.strategy_type,
        config,
      })
      ElMessage.success('策略已创建')
    } else if (editingId.value) {
      await updateIdentityStrategy(editingId.value, { name: form.name, config })
      ElMessage.success('策略已更新')
    }
    dialogVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}

async function toggleActive(row: IdentityStrategy, value: boolean) {
  try {
    await updateIdentityStrategy(row.id, { is_active: value })
    ElMessage.success(value ? '策略已启用' : '策略已停用')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    await refresh()
  }
}

async function remove(row: IdentityStrategy) {
  try {
    await ElMessageBox.confirm(
      `删除策略 "${row.name}" 后, 绑定它的 API Key 将失去身份解析能力 (请求进入非归属模式)。确认删除?`,
      '删除身份策略',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteIdentityStrategy(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

defineExpose({ refresh })
onMounted(refresh)
</script>

<template>
  <div>
    <div class="tab-toolbar">
      <p class="tab-hint">
        策略定义如何从请求中识别参与者: 创建后在「API Key」页把策略绑定到 Key,
        该平台发来的请求即按策略解析身份; 未绑定策略的 Key 进入非归属模式 (不建身份、不读写私有记忆)。
      </p>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        <span>创建策略</span>
      </el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="id"
      empty-text="暂无身份策略"
    >
      <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
      <el-table-column label="类型" width="150">
        <template #default="{ row }">
          <el-tag :type="TYPE_TAG[row.strategy_type as IdentityStrategyType]" size="small">
            {{ row.strategy_type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="90" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.is_active"
            size="small"
            @change="(v: unknown) => toggleActive(row, Boolean(v))"
          />
        </template>
      </el-table-column>
      <el-table-column label="配置" min-width="260">
        <template #default="{ row }">
          <el-tooltip placement="top" :show-after="300" popper-class="config-popper">
            <template #content>
              <pre class="config-pre">{{ prettyConfig(row.config) }}</pre>
            </template>
            <span class="mono muted config-cell">{{ prettyConfig(row.config).slice(0, 60) }}…</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">
          <span class="mono muted">{{ formatDate(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" align="right" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="640px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="例如: AstrBot QQ 群" maxlength="128" show-word-limit />
        </el-form-item>
        <el-form-item label="类型" prop="strategy_type">
          <el-select
            v-model="form.strategy_type"
            :disabled="dialogMode === 'edit'"
            style="width: 100%"
            @change="onTypeChange"
          >
            <el-option
              v-for="t in STRATEGY_TYPES"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            >
              <span>{{ t.label }}</span>
              <span class="option-hint">{{ t.hint }}</span>
            </el-option>
          </el-select>
          <p class="form-item-hint">
            {{ STRATEGY_TYPES.find((t) => t.value === form.strategy_type)?.hint }}
            <template v-if="dialogMode === 'edit'"> (类型创建后不可更改)</template>
          </p>
        </el-form-item>
        <el-form-item label="配置" prop="config">
          <div class="ai-generate-bar">
            <el-button
              link
              type="warning"
              size="small"
              :disabled="aiGenerating"
              @click="aiDescription = aiDescription || 'describe'"
            >
              <el-icon><MagicStick /></el-icon>
              <span> AI 辅助生成</span>
            </el-button>
          </div>

          <div v-if="aiDescription" class="ai-generate-panel">
            <p class="ai-panel-hint">
              用自然语言描述身份信息在消息中的格式, 模型会自动生成正则表达式 (或 LLM prompt)。
            </p>
            <el-form-item label="身份描述" label-width="80px" class="ai-field">
              <el-input
                v-model="aiDescription"
                type="textarea"
                :rows="3"
                placeholder="例如: 每条消息的 system prompt 开头有一行格式为「用户: QQ号=123456, 昵称=小明, 群号=789012」, 请帮我提取 QQ号、昵称和群号"
                :disabled="aiGenerating"
              />
            </el-form-item>
            <el-form-item label="示例消息" label-width="80px" class="ai-field">
              <el-input
                v-model="aiSampleMessage"
                type="textarea"
                :rows="3"
                placeholder="可选: 粘贴一条真实的消息文本, 帮助模型理解格式"
                :disabled="aiGenerating"
              />
            </el-form-item>
            <div class="ai-actions">
              <el-button
                type="primary"
                size="small"
                :loading="aiGenerating"
                @click="aiGenerate"
              >
                生成配置
              </el-button>
              <el-button size="small" :disabled="aiGenerating" @click="resetAiFields">
                收起
              </el-button>
            </div>
            <el-alert
              v-if="aiError"
              :title="aiError"
              type="error"
              :closable="false"
              show-icon
              class="ai-error"
            />
          </div>

          <el-input
            v-model="form.config"
            type="textarea"
            :rows="12"
            class="mono"
            placeholder="JSON 对象"
          />
          <p class="form-item-hint">
            策略特定的 JSON 配置。
            <el-button link type="primary" size="small" @click="useTemplate">
              填入当前类型模板
            </el-button>
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">
          {{ dialogMode === 'create' ? '创建' : '保存' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.tab-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
}

.tab-hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
  max-width: 720px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.config-cell {
  font-size: 12px;
  cursor: help;
}

.form-item-hint {
  margin: $space-1 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.option-hint {
  float: right;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: $space-3;
}

.ai-generate-bar {
  margin-bottom: $space-2;
  text-align: right;
}

.ai-generate-panel {
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
  padding: $space-3;
  margin-bottom: $space-3;
}

.ai-panel-hint {
  margin: 0 0 $space-2;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.ai-field {
  margin-bottom: $space-2;
}

.ai-actions {
  display: flex;
  gap: $space-2;
  margin-bottom: $space-2;
}

.ai-error {
  margin-top: $space-2;
}
</style>

<style lang="scss">
.config-popper {
  max-width: 520px;
}

.config-pre {
  margin: 0;
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
