<script setup lang="ts">
/** 调试聊天页面.
 *
 * 上半: 模拟真实客户端调用 Mnemosync /v1/chat/completions, 附带完整对话历史;
 *        可编辑客户端 system prompt (测试服务器人格 cleansing), 模型下拉可自由输入.
 * 下半: 显示所有 HTTP hop (inbound + upstream), 按 correlation_id 分组的卡片列.
 *
 * 数据源:
 * - session key 由 useDebugStore.activate() 拉取 /panel/admin/debug/session-key
 * - 对话历史由 localStorage 持久 (真实浏览器关闭才清; 我们不主动清 tab)
 * - 调试事件流由 store 的 SSE 通道推送 (App 层不接管, 就本页 mount/unmount)
 */

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useDebugStore } from '@/stores/debug'
import { getDebugEventDetail, listV1Models } from '@/api/client'
import type { ChatMessage, DebugEventDetailResponse } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import ChatArea from '@/components/debug-chat/ChatArea.vue'
import DebugEventList from '@/components/debug-chat/DebugEventList.vue'

const debugStore = useDebugStore()

// ── 对话状态 ──────────────────────────────────────────────────
const CONV_KEY = 'mnemosync_debug_chat_conv'
const SYS_KEY = 'mnemosync_debug_chat_system'
const MODEL_KEY = 'mnemosync_debug_chat_model'
const DEBUG_MODE_KEY = 'mnemosync_debug_chat_debug_mode'

const systemPrompt = ref<string>(localStorage.getItem(SYS_KEY) ?? '')
const useSystem = ref<boolean>(!!systemPrompt.value)
const modelName = ref<string>(localStorage.getItem(MODEL_KEY) || 'mnemosync-any')
const streaming = ref<boolean>(true)
const debugMode = ref<boolean>(localStorage.getItem(DEBUG_MODE_KEY) === '1')
const availableModels = ref<string[]>(['mnemosync-any'])
const conversation = ref<ChatMessage[]>([])
const sending = ref<boolean>(false)

function loadConversation() {
  try {
    const raw = localStorage.getItem(CONV_KEY)
    if (raw) conversation.value = JSON.parse(raw) as ChatMessage[]
  } catch {
    conversation.value = []
  }
}

function saveConversation() {
  localStorage.setItem(CONV_KEY, JSON.stringify(conversation.value))
}

function clearConversation() {
  conversation.value = []
  saveConversation()
  ElMessage.success('对话已清空')
}

watch(systemPrompt, (v) => localStorage.setItem(SYS_KEY, v))
watch(modelName, (v) => localStorage.setItem(MODEL_KEY, v))

// 调试模式切换: 打开 → 订阅 SSE + 拉历史; 关闭 → 断订阅 (但保留 sessionKey 供聊天用)
watch(debugMode, async (on) => {
  localStorage.setItem(DEBUG_MODE_KEY, on ? '1' : '0')
  if (on) {
    await debugStore.activate()
  } else {
    debugStore.deactivate()
  }
})

// ── 生命周期 ──────────────────────────────────────────────────
onMounted(async () => {
  loadConversation()
  // 无论是否开调试, 都要一个 API Key 才能调 /v1/chat/completions
  if (debugMode.value) {
    await debugStore.activate()
  } else {
    await debugStore.ensureKey()
  }
  if (debugStore.sessionKey) {
    try {
      const resp = await listV1Models(debugStore.sessionKey.key)
      availableModels.value = resp.data.map((m) => m.id)
    } catch {
      // 保持默认
    }
  }
})

onBeforeUnmount(() => {
  debugStore.deactivate()
})

// ── 发送逻辑 ───────────────────────────────────────────────────

function buildMessages(userText: string): ChatMessage[] {
  const msgs: ChatMessage[] = []
  if (useSystem.value && systemPrompt.value.trim()) {
    msgs.push({ role: 'system', content: systemPrompt.value })
  }
  for (const m of conversation.value) msgs.push({ role: m.role, content: m.content })
  msgs.push({ role: 'user', content: userText })
  return msgs
}

async function sendMessage(text: string) {
  const key = debugStore.sessionKey?.key
  if (!key) {
    ElMessage.error('未取到调试 API Key, 请稍候或刷新页面')
    return
  }
  sending.value = true
  const messages = buildMessages(text)
  conversation.value.push({ role: 'user', content: text })
  saveConversation()

  // 预先塞一个空 assistant 消息, 流式填充
  const assistantIdx =
    conversation.value.push({ role: 'assistant', content: streaming.value ? '' : '(等待中...)' }) - 1

  try {
    const body = {
      model: modelName.value || 'mnemosync-any',
      messages,
      stream: streaming.value,
    }
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify(body),
    })
    if (!resp.ok) {
      const errText = await resp.text().catch(() => '')
      throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`)
    }
    if (streaming.value && resp.body) {
      const reader = resp.body.getReader()
      const dec = new TextDecoder('utf-8')
      let buf = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let idx: number
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          const line = frame.split('\n').find((l) => l.startsWith('data:'))
          if (!line) continue
          const data = line.slice(5).trim()
          if (data === '[DONE]') continue
          try {
            const obj = JSON.parse(data) as {
              choices?: Array<{ delta?: { content?: string } }>
            }
            const delta = obj.choices?.[0]?.delta?.content
            if (delta) {
              const msg = conversation.value[assistantIdx]
              if (msg) {
                msg.content += delta
              }
            }
          } catch {
            // ignore parse
          }
        }
      }
      saveConversation()
    } else {
      const obj = (await resp.json()) as {
        choices: Array<{ message: { content: string } }>
      }
      const msg = conversation.value[assistantIdx]
      if (msg) msg.content = obj.choices?.[0]?.message?.content ?? ''
      saveConversation()
    }
  } catch (e) {
    const msg = conversation.value[assistantIdx]
    if (msg) msg.content = `[错误] ${(e as Error).message}`
    saveConversation()
  } finally {
    sending.value = false
  }
}

// ── 调试事件详情加载 ───────────────────────────────────────────

async function loadEventDetail(eventId: string): Promise<DebugEventDetailResponse | undefined> {
  try {
    return await getDebugEventDetail(eventId)
  } catch (e) {
    ElMessage.error(`加载详情失败: ${(e as Error).message}`)
    return undefined
  }
}

async function clearAllEvents() {
  try {
    await debugStore.clearAll()
    ElMessage.success('调试日志已清空')
  } catch (e) {
    ElMessage.error(`清空失败: ${(e as Error).message}`)
  }
}
</script>

<template>
  <div class="page-container debug-chat">
    <PageHeader
      title="调试聊天"
    >
      <template #subtitle>
        模拟真实客户端调 Mnemosync <code>/v1/chat/completions</code>; 打开"调试模式"实时观察每一跳 HTTP (客户端 ↔ Mnemosync ↔ 上游), 按 correlation_id 分组
      </template>
      <template #actions>
        <el-tag v-if="debugMode && debugStore.connected" type="success" size="small">SSE 已订阅</el-tag>
        <el-tag v-else-if="debugMode" type="warning" size="small">SSE 连接中…</el-tag>
        <el-tag v-if="debugStore.sessionKey" size="small">
          key: {{ debugStore.sessionKey.key.slice(0, 12) }}…
        </el-tag>
        <div class="switch-container">
          <span class="switch-label">调试模式</span>
          <el-switch v-model="debugMode" inline-prompt />
        </div>
      </template>
    </PageHeader>

    <el-alert v-if="debugStore.error" :title="debugStore.error" type="warning" show-icon />

    <div class="params-bar">
      <el-checkbox v-model="streaming">流式 (stream=true)</el-checkbox>
      <el-checkbox v-model="useSystem">带 system 消息</el-checkbox>
      <span class="params-label">模型:</span>
      <el-select
        v-model="modelName"
        filterable
        allow-create
        default-first-option
        size="small"
        style="width: 220px"
        placeholder="mnemosync-any"
      >
        <el-option v-for="m in availableModels" :key="m" :label="m" :value="m" />
      </el-select>
    </div>

    <div class="split" :class="{ 'split-chat-only': !debugMode }">
      <div class="chat-half">
        <ChatArea
          :conversation="conversation"
          :system-prompt="systemPrompt"
          :use-system="useSystem"
          :model-name="modelName"
          :available-models="availableModels"
          :streaming="streaming"
          :sending="sending"
          @update:system-prompt="(v) => systemPrompt = v"
          @update:use-system="(v) => useSystem = v"
          @update:model-name="(v) => modelName = v"
          @update:streaming="(v) => streaming = v"
          @send="sendMessage"
          @clear="clearConversation"
        />
      </div>

      <div v-if="debugMode" class="debug-half">
        <DebugEventList
          :events-by-correlation="debugStore.eventsByCorrelation"
          :on-load-detail="loadEventDetail"
          @clear-all="clearAllEvents"
        />
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.debug-chat {
  display: flex;
  flex-direction: column;
  gap: $space-3;
  min-height: calc(100vh - #{$header-height} - #{$space-5} * 2);
}

.params-bar {
  display: flex;
  align-items: center;
  gap: $space-3;
  flex-wrap: wrap;
  padding: $space-2 $space-3;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
}

.params-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.split {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: $space-3;
}

.chat-half {
  flex: 1 1 40%;
  min-height: 320px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
  background: var(--el-bg-color);
  padding: $space-3;
}

.split-chat-only .chat-half {
  flex: 1 1 auto;
  min-height: 480px;
}

.debug-half {
  flex: 1 1 60%;
  min-height: 320px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
  background: var(--el-bg-color);
  padding: $space-3;
}

.switch-container {
  display: flex;
  align-items: center;
  gap: $space-1;
  .switch-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

:deep(.page-subtitle code) {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  padding: 0 4px;
  background: var(--el-fill-color-light);
  border-radius: 3px;
}
</style>
