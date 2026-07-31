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
  listIdentityStrategies,
  updateIdentityStrategy,
} from '@/api/client'
import type { IdentityStrategy, IdentityStrategyType } from '@/types/api'
import StrategyConfigForm from './StrategyConfigForm.vue'

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
  {
    value: 'plugin',
    label: 'plugin — 第三方插件',
    hint: '社区开发的平台适配器, 支持消息预处理和群聊上下文合并',
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
  plugin: JSON.stringify(
    {
      plugin_name: 'astrbot',
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
  plugin: 'danger',
}

const items = ref<IdentityStrategy[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance | null>(null)
const editingId = ref<string | null>(null)
const configFormRef = ref<InstanceType<typeof StrategyConfigForm> | null>(null)

const form = reactive({
  name: '',
  strategy_type: 'regex' as IdentityStrategyType,
  config: CONFIG_TEMPLATES.regex,
  tool_policy: {
    enabled: false,
    allowed_tools: '',
    denied_tools: '',
    max_calls_per_round: 5,
    cooldown_seconds: 0,
    global_max_per_window: 0,
    global_window_seconds: 60,
  },
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

function prettyConfig(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function hasToolPolicy(config: string): boolean {
  try {
    const parsed = JSON.parse(config)
    return !!parsed.tool_policy && Object.keys(parsed.tool_policy).length > 0
  } catch {
    return false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  editingId.value = null
  form.name = ''
  form.strategy_type = 'regex'
  form.config = CONFIG_TEMPLATES.regex
  form.tool_policy = {
    enabled: false,
    allowed_tools: '',
    denied_tools: '',
    max_calls_per_round: 5,
    cooldown_seconds: 0,
    global_max_per_window: 0,
    global_window_seconds: 60,
  }
  dialogVisible.value = true
}

async function openEdit(row: IdentityStrategy) {
  dialogMode.value = 'edit'
  editingId.value = row.id
  form.name = row.name
  form.strategy_type = row.strategy_type

  // 解析 tool_policy
  try {
    const parsed = JSON.parse(row.config)
    const tp = parsed.tool_policy || {}
    form.tool_policy = {
      enabled: !!tp && Object.keys(tp).length > 0,
      allowed_tools: (tp.allowed_tools || []).join(', '),
      denied_tools: (tp.denied_tools || []).join(', '),
      max_calls_per_round: tp.max_calls_per_round ?? 5,
      cooldown_seconds: tp.cooldown_seconds ?? 0,
      global_max_per_window: tp.global_max_per_window ?? 0,
      global_window_seconds: tp.global_window_seconds ?? 60,
    }
  } catch {
    // ignore
  }

  // 设置配置
  form.config = prettyConfig(row.config)

  // 加载插件列表（如果是 plugin 类型）
  if (row.strategy_type === 'plugin') {
    await configFormRef.value?.loadPlugins()
    try {
      const parsed = JSON.parse(row.config)
      configFormRef.value!.pluginSelected = parsed.plugin_name || ''
    } catch {
      configFormRef.value!.pluginSelected = ''
    }
  } else {
    configFormRef.value?.configToFields(row.config)
  }

  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function onTypeChange(value: IdentityStrategyType) {
  const oldTemplates = Object.values(CONFIG_TEMPLATES)
  if (oldTemplates.includes(form.config.trim())) {
    form.config = CONFIG_TEMPLATES[value]
  }
  configFormRef.value?.resetConfigFields(value)
  if (value !== 'plugin') {
    form.config = configFormRef.value
      ? JSON.parse(form.config).frontend
        ? form.config
        : CONFIG_TEMPLATES[value]
      : CONFIG_TEMPLATES[value]
  }
}

function buildConfigWithToolPolicy(): string {
  const baseConfig = form.config
  let config: Record<string, unknown>
  try {
    config = JSON.parse(baseConfig || '{}')
  } catch {
    config = {}
  }
  if (form.tool_policy.enabled) {
    config.tool_policy = {
      allowed_tools: form.tool_policy.allowed_tools
        .split(',')
        .map((t: string) => t.trim())
        .filter(Boolean),
      denied_tools: form.tool_policy.denied_tools
        .split(',')
        .map((t: string) => t.trim())
        .filter(Boolean),
      max_calls_per_round: form.tool_policy.max_calls_per_round,
      cooldown_seconds: form.tool_policy.cooldown_seconds,
      global_max_per_window: form.tool_policy.global_max_per_window,
      global_window_seconds: form.tool_policy.global_window_seconds,
    }
  }
  return JSON.stringify(config, null, 2)
}

async function submit() {
  if (!formRef.value || submitting.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const config = buildConfigWithToolPolicy()
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

defineExpose({ refresh })

onMounted(() => {
  refresh()
})
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
      <el-table-column label="工具策略" width="100" align="center">
        <template #default="{ row }">
          <el-tag
            :type="hasToolPolicy(row.config) ? 'success' : 'info'"
            size="small"
          >
            {{ hasToolPolicy(row.config) ? '已配置' : '未配置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="配置" min-width="200">
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
            class="full-width"
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
          <p class="form-hint">
            {{ STRATEGY_TYPES.find((t) => t.value === form.strategy_type)?.hint }}
            <template v-if="dialogMode === 'edit'"> (类型创建后不可更改)</template>
          </p>
        </el-form-item>

        <el-form-item label="配置" prop="config" class="config-section">
          <StrategyConfigForm
            ref="configFormRef"
            :strategy-type="form.strategy_type"
            :config="form.config"
            :tool-policy="form.tool_policy"
            @update:config="(v: string) => (form.config = v)"
            @update:tool-policy="(v: typeof form.tool_policy) => (form.tool_policy = v)"
          />
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

.full-width {
  width: 100%;
}

.form-hint {
  margin: 4px 0 0;
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
