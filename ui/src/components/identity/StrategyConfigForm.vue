<script setup lang="ts">
/**
 * 策略配置表单组件
 * 根据策略类型动态展示对应的配置字段
 */
import { computed, reactive, ref, watch } from 'vue'
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
      search_in: 'last_user',
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
  search_in: 'last_user' as string,
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

// Regex 测试面板
const regexTestText = ref('')
const regexTestMatch = computed(() => {
  const text = regexTestText.value
  return {
    actor: testPattern(text, configFields.actor_pattern),
    name: testPattern(text, configFields.name_pattern),
    space: testPattern(text, configFields.space_pattern),
    eventId: testPattern(text, configFields.event_id_pattern),
  }
})

function testPattern(text: string, pattern: string): string | null {
  if (!text || !pattern) return null
  try {
    const m = new RegExp(pattern).exec(text)
    return m?.[1] ?? null
  } catch {
    return null
  }
}

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
    configFields.search_in = cfg.search_in || 'last_user'
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
        external_key: 'local-user',
        frontend: 'api_key_bound',
        display_name: configFields.display_name || '本地用户',
        channel_type: 'direct',
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
  configFields.search_in = defaults.search_in || 'last_user'
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
        <el-form-item label="显示名" class="config-field">
          <el-input
            v-model="configFields.display_name"
            placeholder="例如: 本地用户 / Cherry Studio"
            @input="onConfigFieldChange"
          />
        </el-form-item>
      </div>
      <p class="form-hint">
        此 Key 的请求将始终识别为此用户（固定身份）。适用于 ChatBox、Cherry Studio 等单用户本地应用。
        外部标识和频道类型已自动配置，无需手动设置。
      </p>
    </template>

    <!-- regex 类型 -->
    <template v-else-if="strategyType === 'regex'">
      <p class="form-intro">
        从消息文本中用<a href="https://github.com/cdoco/learn-regex-zh" target="_blank">正则表达式</a>提取身份。每条正则必须包含<strong>一个捕获组 <code>()</code></strong>，系统会提取括号内匹配到的值
      </p>
      <div class="field-group">
        <el-form-item label="前端应用" class="config-field">
          <el-input
            v-model="configFields.frontend"
            placeholder="例如: astrbot"
            @input="onConfigFieldChange"
          />
          <p class="field-help">标识来源平台，如 astrbot、maibot、discord 等</p>
        </el-form-item>
        <el-form-item label="用户标识" class="config-field" required>
          <el-input
            v-model="configFields.actor_pattern"
            placeholder="QQ号[:：]\\s*(\\d+)"
            @input="onConfigFieldChange"
          />
          <p class="field-help">提取用户唯一 ID（必填）。例：QQ号、Discord ID、用户名</p>
        </el-form-item>
        <el-form-item label="显示名称" class="config-field">
          <el-input
            v-model="configFields.name_pattern"
            placeholder="昵称[:：]\\s*(\\S+)"
            @input="onConfigFieldChange"
          />
          <p class="field-help">提取用户昵称（可选），用于展示</p>
        </el-form-item>
        <el-form-item label="群聊 ID" class="config-field">
          <el-input
            v-model="configFields.space_pattern"
            placeholder="群号[:：]\\s*(\\d+)"
            @input="onConfigFieldChange"
          />
          <p class="field-help">提取群聊 / 会话 ID（可选）。匹配到时频道类型自动设为「群聊」</p>
        </el-form-item>
        <el-form-item label="消息 ID" class="config-field">
          <el-input
            v-model="configFields.event_id_pattern"
            placeholder="消息ID[:：]\\s*(\\S+)"
            @input="onConfigFieldChange"
          />
          <p class="field-help">提取消息唯一 ID（可选），用于幂等去重</p>
        </el-form-item>
        <el-form-item label="搜索范围" class="config-field">
          <el-select v-model="configFields.search_in" @change="onConfigFieldChange">
            <el-option label="System 消息" value="system" />
            <el-option label="最后一条用户消息" value="last_user" />
            <el-option label="所有消息" value="all" />
          </el-select>
          <p class="field-help">决定从消息列表的哪个部分提取文本进行正则匹配</p>
        </el-form-item>
      </div>

      <!-- 测试提取面板 -->
      <el-divider content-position="left">
        <span class="divider-label">测试提取（可选）</span>
      </el-divider>
      <div class="regex-test-section">
        <p class="test-hint">粘贴一条真实消息文本，实时预览正则提取结果：</p>
        <el-input
          v-model="regexTestText"
          type="textarea"
          :rows="3"
          placeholder="例: [system] 用户: QQ号=123456, 昵称=小明, 群号=789012"
        />
        <div v-if="regexTestText && configFields.actor_pattern" class="test-results">
          <div class="test-result-row">
            <span class="test-label">用户标识：</span>
            <code v-if="regexTestMatch.actor" class="test-value match">{{ regexTestMatch.actor }}</code>
            <span v-else class="test-value no-match">未匹配</span>
          </div>
          <div v-if="configFields.name_pattern" class="test-result-row">
            <span class="test-label">显示名称：</span>
            <code v-if="regexTestMatch.name" class="test-value match">{{ regexTestMatch.name }}</code>
            <span v-else class="test-value no-match">未匹配</span>
          </div>
          <div v-if="configFields.space_pattern" class="test-result-row">
            <span class="test-label">群聊 ID：</span>
            <code v-if="regexTestMatch.space" class="test-value match">{{ regexTestMatch.space }}</code>
            <span v-else class="test-value no-match">未匹配</span>
          </div>
          <div v-if="configFields.event_id_pattern" class="test-result-row">
            <span class="test-label">消息 ID：</span>
            <code v-if="regexTestMatch.eventId" class="test-value match">{{ regexTestMatch.eventId }}</code>
            <span v-else class="test-value no-match">未匹配</span>
          </div>
        </div>
        <p v-else-if="regexTestText && !configFields.actor_pattern" class="test-hint" style="color: var(--el-color-warning)">
          请先填写用户标识正则
        </p>
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

.form-intro {
  margin: 0 0 $space-3;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;

  code {
    background: var(--el-fill-color);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 12px;
  }
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

.field-help {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

/* Regex 测试面板 */
.regex-test-section {
  padding: $space-3;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
}

.test-hint {
  margin: 0 0 $space-2;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.test-results {
  margin-top: $space-2;
  display: flex;
  flex-direction: column;
  gap: $space-1;
}

.test-result-row {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-size: 13px;
}

.test-label {
  color: var(--el-text-color-secondary);
  min-width: 70px;
  flex-shrink: 0;
}

.test-value {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;

  &.match {
    color: var(--el-color-success);
    background: var(--el-color-success-light-9);
    padding: 1px 6px;
    border-radius: 3px;
  }

  &.no-match {
    color: var(--el-text-color-placeholder);
    font-style: italic;
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
