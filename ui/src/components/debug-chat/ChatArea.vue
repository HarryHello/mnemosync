<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage } from '@/types/api'

const props = defineProps<{
  conversation: ChatMessage[]
  systemPrompt: string
  useSystem: boolean
  modelName: string
  availableModels: string[]
  streaming: boolean
  sending: boolean
  debugMode: boolean
}>()

const emit = defineEmits<{
  'update:systemPrompt': [value: string]
  'update:useSystem': [value: boolean]
  'update:modelName': [value: string]
  'update:streaming': [value: boolean]
  'update:debugMode': [value: boolean]
  send: [text: string]
  clear: []
}>()

// 本地状态
const localSystemPrompt = ref(props.systemPrompt)
const localUseSystem = ref(props.useSystem)
const localModelName = ref(props.modelName)
const localStreaming = ref(props.streaming)
const input = ref<string>('')
const messagesEl = ref<HTMLElement | null>(null)

// 监听 prop 变化更新本地状态
watch(() => props.systemPrompt, (v) => { localSystemPrompt.value = v })
watch(() => props.useSystem, (v) => { localUseSystem.value = v })
watch(() => props.modelName, (v) => { localModelName.value = v })
watch(() => props.streaming, (v) => { localStreaming.value = v })

// 监听本地状态变化 emit 更新
watch(localSystemPrompt, (v) => emit('update:systemPrompt', v))
watch(localUseSystem, (v) => emit('update:useSystem', v))
watch(localModelName, (v) => emit('update:modelName', v))
watch(localStreaming, (v) => emit('update:streaming', v))

async function scrollBottom() {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}

// 监听 conversation 变化滚动到底部
watch(() => props.conversation.length, () => {
  scrollBottom()
}, { deep: true })

function send() {
  const text = input.value.trim()
  if (!text || props.sending) return
  emit('send', text)
  input.value = ''
}

function clearConversation() {
  emit('clear')
}
</script>

<template>
  <div class="chat-area">
    <div v-if="localUseSystem" class="system-editor">
      <div class="section-label">客户端 system prompt (测试服务器人格清洗)</div>
      <el-input
        v-model="localSystemPrompt"
        type="textarea"
        :rows="2"
        placeholder="e.g. 你是一个海盗风格的助手"
      />
    </div>

    <div ref="messagesEl" class="messages">
      <div v-for="(m, i) in conversation" :key="i" class="msg" :class="`role-${m.role}`">
        <div class="msg-content">{{ m.content }}</div>
      </div>
      <div v-if="!conversation.length" class="empty">发一句话看看流水线在跑什么。</div>
    </div>

    <div class="composer">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        :disabled="sending"
        placeholder="输入消息, Enter 发送 (Shift+Enter 换行)"
        @keydown.enter.exact.prevent="send"
        @keydown.meta.enter="send"
        @keydown.ctrl.enter="send"
      />
      <div class="composer-bottom">
        <div class="debug-switch">
          <span class="switch-label">调试模式</span>
          <el-switch :model-value="debugMode" @update:model-value="(v: boolean) => emit('update:debugMode', v)" inline-prompt />
        </div>
        <div class="composer-btns">
          <el-button size="small" @click="clearConversation" round>清空对话</el-button>
          <el-button type="primary" size="small" :loading="sending" @click="send" round>发送</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  height: 100%;
}

.system-editor {
  flex-shrink: 0;
  margin-bottom: $space-2;
}

.section-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: $space-2;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-sm;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.msg {
  padding: $space-2 $space-3;
  border-radius: $radius-sm;
  background: var(--el-bg-color);
  border: 1px solid transparent;
  flex-shrink: 0;

  &.role-user {
    background: var(--el-color-primary-light-7);
    max-width: 80%;
    align-self: flex-end;
  }

  &.role-assistant {
    background: var(--el-color-success-light-7);
    max-width: 80%;
    align-self: flex-start;
  }

  &.role-system {
    border-color: var(--el-color-warning-light-7);
    max-width: 80%;
    align-self: center;
  }
}

.msg-role {
  font-size: 11px;
  text-transform: uppercase;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.msg-content { white-space: pre-wrap; word-break: break-word; }

.composer {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  flex-shrink: 0;
}
.composer-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.debug-switch {
  display: flex;
  align-items: center;
  gap: $space-1;
}
.switch-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.composer-btns { display: flex; justify-content: flex-end; gap: $space-2; }

.empty {
  color: var(--el-text-color-secondary);
  padding: $space-4;
  text-align: center;
  flex-shrink: 0;
}
</style>
