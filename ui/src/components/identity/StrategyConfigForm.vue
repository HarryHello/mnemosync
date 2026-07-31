<script setup lang="ts">
/**
 * 策略配置表单组件
 * 根据策略类型动态展示对应的配置字段
 */
import { reactive, ref, watch } from 'vue'
import type { IdentityStrategyType, PluginInfo } from '@/types/api'
import { generateStrategyConfig, listPlugins } from '@/api/client'

interface ToolPolicyForm {
  enabled: boolean
  allowed_tools: string
  denied_tools: string
  max_calls_per_round: number
  cooldown_seconds: number
  global_max_per_window: number
  global_window_seconds: number
}

const props = defineProps<{
  strategyType: IdentityStrategyType
  config: string
  toolPolicy: ToolPolicyForm
}>()

const emit = defineEmits<{
  'update:config': [value: string]
  'update:toolPolicy': [value: ToolPolicyForm]
}>()

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

const configFields = reactive({
  frontend: 'web',
  external_key: 'local-user',
  display_name: '本地用户',
  channel_type: 'direct' as string,
  actor_pattern: '',
  name_pattern: '',
  space_pattern: '',
  event_id_pattern: '',
  search_in: 'system_or_first_user' as string,
  prompt_template: '',
})

const pluginOptions = ref<PluginInfo[]>([])
const pluginLoading = ref(false)
const pluginSelected = ref('')

// AI 辅助生成
const aiGenerating = ref(false)
const aiDescription = ref('')
const aiSampleMessage = ref('')
const aiError = ref<string | null>(null)
const showAiPanel = ref(false)

function configToFields(jsonStr: string) {
  try {
    const cfg = JSON.parse(jsonStr || '{}')
    configFields.frontend = cfg.frontend || ''
    configFields.external_key = cfg.external_key || ''
    configFields.display_name = cfg.display_name || ''
    configFields.channel_type = cfg.channel_type || 'direct'
    configFields.actor_pattern = cfg.actor_pattern || ''
    configFields.name_pattern = cfg.name_pattern || ''
    configFields.space_pattern = cfg.space_pattern || ''
    configFields.event_id_pattern = cfg.event_id_pattern || ''
    configFields.search_in = cfg.search_in || 'system_or_first_user'
    configFields.prompt_template = cfg.prompt_template || ''
  } catch {
    // ignore
  }
}

function fieldsToConfig(): string {
  switch (props.strategyType) {
    case 'direct':
      return JSON.stringify({ frontend: configFields.frontend || 'web' }, null, 2)
    case 'api_key_bound':
      return JSON.stringify({
        external_key: configFields.external_key || 'local-user',
        frontend: configFields.frontend || 'chatbox',
        display_name: configFields.display_name || '本地用户',
        channel_type: configFields.channel_type || 'direct',
      }, null, 2)
    case 'regex':
      return JSON.stringify({
        frontend: configFields.frontend || 'astrbot',
        actor_pattern: configFields.actor_pattern || '',
        name_pattern: configFields.name_pattern || '',
        space_pattern: configFields.space_pattern || '',
        event_id_pattern: configFields.event_id_pattern || '',
        search_in: configFields.search_in || 'system_or_first_user',
      }, null, 2)
    case 'llm':
      return JSON.stringify({
        frontend: configFields.frontend || 'custom-bot',
        prompt_template: configFields.prompt_template || '',
      }, null, 2)
    default:
      return props.config
  }
}

function onConfigFieldChange() {
  emit('update:config', fieldsToConfig())
}

function resetConfigFields(type: IdentityStrategyType) {
  if (type === 'plugin') return
  const defaults = JSON.parse(CONFIG_TEMPLATES[type] || '{}')
  configFields.frontend = defaults.frontend || ''
  configFields.external_key = defaults.external_key || ''
  configFields.display_name = defaults.display_name || ''
  configFields.channel_type = defaults.channel_type || 'direct'
  configFields.actor_pattern = defaults.actor_pattern || ''
  configFields.name_pattern = defaults.name_pattern || ''
  configFields.space_pattern = defaults.space_pattern || ''
  configFields.event_id_pattern = defaults.event_id_pattern || ''
  configFields.search_in = defaults.search_in || 'system_or_first_user'
  configFields.prompt_template = defaults.prompt_template || ''
}

async function loadPlugins() {
  pluginLoading.value = true
  try {
    const res = await listPlugins()
    pluginOptions.value = res.items
  } catch {
    pluginOptions.value = []
  } finally {
    pluginLoading.value = false
  }
}

function onPluginSelected(name: string) {
  emit('update:config', JSON.stringify({ plugin_name: name }, null, 2))
}

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
      strategy_type: props.strategyType,
      description: desc,
      sample_message: aiSampleMessage.value.trim() || null,
    })
    emit('update:config', resp.config)
    configToFields(resp.config)
    showAiPanel.value = false
    aiDescription.value = ''
    aiSampleMessage.value = ''
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
  showAiPanel.value = false
}

function onToolPolicyFieldChange<K extends keyof ToolPolicyForm>(key: K, value: ToolPolicyForm[K]) {
  emit('update:toolPolicy', { ...props.toolPolicy, [key]: value })
}

// 监听策略类型变化，plugin 类型自动加载插件列表
watch(
  () => props.strategyType,
  (type) => {
    if (type === 'plugin') {
      void loadPlugins()
    }
  },
)

// 暴露方法给父组件
defineExpose({
  resetConfigFields,
  configToFields,
  loadPlugins,
  pluginSelected,
})
</script>

<template>
  <div class="strategy-config-form">
    <!-- AI 辅助生成 -->
    <div v-if="strategyType !== 'plugin'" class="ai-section">
      <el-button
        v-if="!showAiPanel"
        link
        type="primary"
        size="small"
        @click="showAiPanel = true"
      >
        <el-icon><MagicStick /></el-icon>
        <span>AI 辅助生成配置</span>
      </el-button>

      <div v-else class="ai-panel">
        <div class="ai-panel-header">
          <span class="ai-panel-title">
            <el-icon><MagicStick /></el-icon>
            AI 辅助生成
          </span>
          <el-button link type="info" size="small" @click="resetAiFields">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <p class="ai-hint">
          用自然语言描述身份信息在消息中的格式, 模型会自动生成配置。
        </p>
        <el-form-item label="身份描述" class="ai-field">
          <el-input
            v-model="aiDescription"
            type="textarea"
            :rows="2"
            placeholder="例如: 每条消息的 system prompt 开头有一行格式为「用户: QQ号=123456, 昵称=小明, 群号=789012」"
            :disabled="aiGenerating"
          />
        </el-form-item>
        <el-form-item label="示例消息" class="ai-field">
          <el-input
            v-model="aiSampleMessage"
            type="textarea"
            :rows="2"
            placeholder="可选: 粘贴一条真实的消息文本"
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
            取消
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
    </div>

    <!-- 插件选择 -->
    <template v-if="strategyType === 'plugin'">
      <el-select
        v-model="pluginSelected"
        placeholder="选择插件"
        class="full-width"
        :loading="pluginLoading"
        @change="onPluginSelected"
      >
        <el-option
          v-for="p in pluginOptions"
          :key="p.name"
          :label="p.name + (p.description ? ' — ' + p.description : '')"
          :value="p.name"
        />
      </el-select>
      <p class="form-hint">
        从已发现的身份解析插件中选择。插件自动加载自 plugins/ 目录。
      </p>
    </template>

    <!-- direct 类型 -->
    <template v-else-if="strategyType === 'direct'">
      <el-form-item label="前端应用" class="config-field">
        <el-input
          v-model="configFields.frontend"
          placeholder="例如: web"
          @input="onConfigFieldChange"
        />
        <template #label>
          <span class="field-label">前端应用</span>
        </template>
      </el-form-item>
      <p class="form-hint">标识信息来源的应用名称。</p>
    </template>

    <!-- api_key_bound 类型 -->
    <template v-else-if="strategyType === 'api_key_bound'">
      <div class="field-group">
        <el-form-item label="前端应用" class="config-field">
          <el-input
            v-model="configFields.frontend"
            placeholder="例如: chatbox"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="外部标识" class="config-field">
          <el-input
            v-model="configFields.external_key"
            placeholder="例如: local-user"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="显示名" class="config-field">
          <el-input
            v-model="configFields.display_name"
            placeholder="例如: 本地用户"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="频道类型" class="config-field">
          <el-select v-model="configFields.channel_type" @change="onConfigFieldChange">
            <el-option label="私聊 (direct)" value="direct" />
            <el-option label="群聊 (group)" value="group" />
          </el-select>
        </el-form-item>
      </div>
    </template>

    <!-- regex 类型 -->
    <template v-else-if="strategyType === 'regex'">
      <div class="field-group">
        <el-form-item label="前端应用" class="config-field">
          <el-input
            v-model="configFields.frontend"
            placeholder="例如: astrbot"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="用户正则" class="config-field">
          <el-input
            v-model="configFields.actor_pattern"
            placeholder="QQ号[:：]\\s*(\\d+)"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="名称正则" class="config-field">
          <el-input
            v-model="configFields.name_pattern"
            placeholder="用户名[:：]\\s*(\\S+)"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="空间正则" class="config-field">
          <el-input
            v-model="configFields.space_pattern"
            placeholder="群号[:：]\\s*(\\d+)"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="事件ID正则" class="config-field">
          <el-input
            v-model="configFields.event_id_pattern"
            placeholder="消息ID[:：]\\s*(\\S+)"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="搜索范围" class="config-field">
          <el-select v-model="configFields.search_in" @change="onConfigFieldChange">
            <el-option label="System 或第一条用户消息" value="system_or_first_user" />
            <el-option label="最后一条用户消息" value="last_user" />
            <el-option label="所有 System 消息" value="all_system" />
          </el-select>
        </el-form-item>
      </div>
    </template>

    <!-- llm 类型 -->
    <template v-else-if="strategyType === 'llm'">
      <div class="field-group">
        <el-form-item label="前端应用" class="config-field">
          <el-input
            v-model="configFields.frontend"
            placeholder="例如: custom-bot"
            @input="onConfigFieldChange"
          />
        </el-form-item>
        <el-form-item label="提示模板" class="config-field">
          <el-input
            v-model="configFields.prompt_template"
            type="textarea"
            :rows="4"
            placeholder="从以下对话中识别发言者身份..."
            @input="onConfigFieldChange"
          />
        </el-form-item>
      </div>
    </template>

    <!-- 工具策略 -->
    <el-divider content-position="left">
      <span class="divider-label">工具策略（可选）</span>
    </el-divider>

    <div class="tool-policy-section">
      <el-form-item class="tool-policy-toggle">
        <el-switch
          :model-value="toolPolicy.enabled"
          @change="(v: unknown) => onToolPolicyFieldChange('enabled', Boolean(v))"
        />
        <span class="toggle-label">启用工具限制</span>
      </el-form-item>

      <Transition name="slide-fade">
        <div v-if="toolPolicy.enabled" class="tool-policy-fields">
          <el-form-item label="允许工具" class="policy-field">
            <el-input
              :model-value="toolPolicy.allowed_tools"
              placeholder="白名单: poke, react (逗号分隔, 留空=全部允许)"
              @input="(v: string) => onToolPolicyFieldChange('allowed_tools', v)"
            />
          </el-form-item>
          <el-form-item label="禁止工具" class="policy-field">
            <el-input
              :model-value="toolPolicy.denied_tools"
              placeholder="黑名单: kick, ban, mute (逗号分隔)"
              @input="(v: string) => onToolPolicyFieldChange('denied_tools', v)"
            />
          </el-form-item>

          <div class="policy-row">
            <el-form-item label="每轮上限" class="policy-field">
              <el-input-number
                :model-value="toolPolicy.max_calls_per_round"
                :min="1"
                :max="10"
                controls-position="right"
                @change="(v: number | undefined) => onToolPolicyFieldChange('max_calls_per_round', v ?? 5)"
              />
              <span class="field-hint">单次请求最大调用数</span>
            </el-form-item>

            <el-form-item label="冷却秒数" class="policy-field">
              <el-input-number
                :model-value="toolPolicy.cooldown_seconds"
                :min="0"
                :max="3600"
                :step="10"
                controls-position="right"
                @change="(v: number | undefined) => onToolPolicyFieldChange('cooldown_seconds', v ?? 0)"
              />
              <span class="field-hint">同工具调用间隔（0=不限）</span>
            </el-form-item>
          </div>

          <div class="policy-row">
            <el-form-item label="全局上限" class="policy-field">
              <el-input-number
                :model-value="toolPolicy.global_max_per_window"
                :min="0"
                :max="1000"
                :step="5"
                controls-position="right"
                @change="(v: number | undefined) => onToolPolicyFieldChange('global_max_per_window', v ?? 0)"
              />
              <span class="field-hint">窗口内总调用数（0=不限）</span>
            </el-form-item>

            <el-form-item label="统计窗口" class="policy-field">
              <el-input-number
                :model-value="toolPolicy.global_window_seconds"
                :min="1"
                :max="3600"
                :step="10"
                controls-position="right"
                :disabled="toolPolicy.global_max_per_window === 0"
                @change="(v: number | undefined) => onToolPolicyFieldChange('global_window_seconds', v ?? 60)"
              />
              <span class="field-hint">频率限制的统计窗口（秒）</span>
            </el-form-item>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.strategy-config-form {
  width: 100%;

  :deep(.el-form-item) {
    margin-bottom: 16px;
  }
}

/* AI 辅助生成区域 */
.ai-section {
  margin-bottom: 16px;
  text-align: right;
}

.ai-panel {
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
  padding: $space-3;
  text-align: left;
}

.ai-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: $space-2;
}

.ai-panel-title {
  display: flex;
  align-items: center;
  gap: $space-1;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.ai-hint {
  margin: 0 0 $space-2;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.ai-field {
  margin-bottom: $space-2 !important;
}

.ai-actions {
  display: flex;
  gap: $space-2;
  margin-top: $space-2;
}

.ai-error {
  margin-top: $space-2;
}

/* 表单字段 */
.full-width {
  width: 100%;
}

.form-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.field-group {
  :deep(.el-form-item__label) {
    font-size: 13px;
  }
}

.config-field {
  :deep(.el-form-item__content) {
    display: block;
  }
}

/* 工具策略区域 */
.tool-policy-section {
  padding: 0 4px;
}

.tool-policy-toggle {
  :deep(.el-form-item__content) {
    display: flex;
    align-items: center;
    gap: $space-2;
  }
}

.toggle-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.tool-policy-fields {
  padding-top: $space-2;
  border-top: 1px dashed var(--el-border-color-lighter);
}

.policy-row {
  display: flex;
  gap: $space-4;

  .policy-field {
    flex: 1;
  }
}

.policy-field {
  :deep(.el-form-item__content) {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: $space-1;
  }
}

.field-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

/* divider 样式 */
.divider-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-regular);
}

/* 动画 */
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.2s ease;
}

.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
