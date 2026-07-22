<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ChatMessage } from '@/types/api'

const props = defineProps<{
  conversation: ChatMessage[]
  systemPrompt: string
  useSystem: boolean
  modelName: string
  availableModels: string[]
  streaming: boolean
  sending: boolean
}>()

const emit = defineEmits<{
  'update:conversation': [value: ChatMessage[]]
  'update:systemPrompt': [value: string]
  'update:useSystem': [value: boolean]
  'update:modelName': [value: string]
  'update:streaming': [value: boolean]
  send: [text: string]
  clear: []
}>()

const input = ref<string>('')
const messagesEl = ref<HTMLElement | null>(null)

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
    <div v-if="useSystem" class="system-editor">
      <div class="section-label">客户端 system prompt (测试服务器人格清洗)</div>
      <el-input
        v-model="systemPrompt"
        type="textarea"
        :rows="2"
        placeholder="e.g. 你是一个海盗风格的助手"
        @update:model-value="(v) => emit('update:systemPrompt', v)"
      />
    </div>

    <div ref="messagesEl" class="messages">
      <div v-for="(m, i) in conversation" :key="i" class="msg" :class="`role-${m.role}`">
        <div class="msg-role">{{ m.role }}</div>
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
      <div class="composer-btns">
        <el-button size="small" @click="clearConversation">清空对话</el-button>
        <el-button type="primary" size="small" :loading="sending" @click="send">发送</el-button>
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

.system-editor { margin-bottom: $space-2; }

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
  border: 1px solid var(--el-border-color-lighter);

  &.role-user { border-color: var(--el-color-primary-light-7); }
  &.role-assistant { border-color: var(--el-color-success-light-7); }
  &.role-system { border-color: var(--el-color-warning-light-7); }
}

.msg-role {
  font-size: 11px;
  text-transform: uppercase;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.msg-content { white-space: pre-wrap; word-break: break-word; }

.composer { display: flex; flex-direction: column; gap: $space-2; }
.composer-btns { display: flex; justify-content: flex-end; gap: $space-2; }

.empty {
  color: var(--el-text-color-secondary);
  padding: $space-4;
  text-align: center;
}
</style>
