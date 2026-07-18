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

import { computed, onBeforeUnmount, onMounted, ref, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useDebugStore } from '@/stores/debug'
import { getDebugEventDetail, listV1Models } from '@/api/client'
import type {
  DebugEventDetailResponse,
  DebugEventSummary,
  ChatMessage,
} from '@/types/api'

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
const input = ref<string>('')
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

const messagesEl = ref<HTMLElement | null>(null)

async function scrollBottom() {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}

function buildMessages(userText: string): ChatMessage[] {
  const msgs: ChatMessage[] = []
  if (useSystem.value && systemPrompt.value.trim()) {
    msgs.push({ role: 'system', content: systemPrompt.value })
  }
  for (const m of conversation.value) msgs.push({ role: m.role, content: m.content })
  msgs.push({ role: 'user', content: userText })
  return msgs
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  const key = debugStore.sessionKey?.key
  if (!key) {
    ElMessage.error('未取到调试 API Key, 请稍候或刷新页面')
    return
  }
  sending.value = true
  const messages = buildMessages(text)
  input.value = ''
  conversation.value.push({ role: 'user', content: text })
  saveConversation()
  await scrollBottom()

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
                await scrollBottom()
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
      await scrollBottom()
    }
  } catch (e) {
    const msg = conversation.value[assistantIdx]
    if (msg) msg.content = `[错误] ${(e as Error).message}`
    saveConversation()
  } finally {
    sending.value = false
  }
}

// ── 调试事件展示 ───────────────────────────────────────────────

interface EventGroup {
  correlation_id: string
  events: DebugEventSummary[]
  first_ts: number
}

const groups = computed<EventGroup[]>(() => {
  const map = debugStore.eventsByCorrelation
  const arr: EventGroup[] = []
  for (const [cid, evs] of map.entries()) {
    arr.push({ correlation_id: cid, events: evs, first_ts: evs[0]?.ts ?? 0 })
  }
  arr.sort((a, b) => b.first_ts - a.first_ts) // 新的在上
  return arr
})

const expanded = ref<Set<string>>(new Set())
const details = ref<Map<string, DebugEventDetailResponse>>(new Map())
const detailLoading = ref<Set<string>>(new Set())

async function toggle(eventId: string) {
  const s = new Set(expanded.value)
  if (s.has(eventId)) {
    s.delete(eventId)
  } else {
    s.add(eventId)
    if (!details.value.has(eventId)) {
      detailLoading.value.add(eventId)
      try {
        const d = await getDebugEventDetail(eventId)
        details.value.set(eventId, d)
      } catch (e) {
        ElMessage.error(`加载详情失败: ${(e as Error).message}`)
      } finally {
        detailLoading.value.delete(eventId)
      }
    }
  }
  expanded.value = s
}

async function clearAll() {
  try {
    await debugStore.clearAll()
    expanded.value = new Set()
    details.value = new Map()
    ElMessage.success('调试日志已清空')
  } catch (e) {
    ElMessage.error(`清空失败: ${(e as Error).message}`)
  }
}

function directionLabel(d: string): string {
  const map: Record<string, string> = {
    inbound_request: '入站请求',
    inbound_response: '入站响应',
    upstream_request: '上游请求',
    upstream_response: '上游响应',
    upstream_request_final: '上游请求(完成)',
    upstream_response_final: '上游流式(汇总)',
  }
  return map[d] ?? d
}

function directionTag(d: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (d.startsWith('inbound_request')) return 'info'
  if (d.startsWith('inbound_response')) return 'primary'
  if (d.startsWith('upstream_request')) return 'warning'
  if (d.startsWith('upstream_response')) return 'success'
  return 'info'
}

function formatBody(body: unknown): string {
  if (body === null || body === undefined) return ''
  if (typeof body === 'string') return body
  try {
    return JSON.stringify(body, null, 2)
  } catch {
    return String(body)
  }
}

function formatTs(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
</script>

<template>
  <div class="page-container debug-chat">
    <div class="page-head">
      <div class="page-head-text">
        <h2 class="page-title">调试聊天</h2>
        <p class="page-subtitle">
          模拟真实客户端调 Mnemosync <code>/v1/chat/completions</code>; 打开"调试模式"实时观察每一跳 HTTP (客户端 ↔ Mnemosync ↔ 上游), 按 correlation_id 分组
        </p>
      </div>

    </div>

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

    <el-alert v-if="debugStore.error" :title="debugStore.error" type="warning" show-icon class="err" />

    <div class="split" :class="{ 'split-chat-only': !debugMode }">
      <div class="chat-half">
        <div v-if="useSystem" class="system-editor">
          <div class="section-label">客户端 system prompt (测试服务器人格清洗)</div>
          <el-input
            v-model="systemPrompt"
            type="textarea"
            :rows="2"
            placeholder="e.g. 你是一个海盗风格的助手"
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

      <div class="page-head-actions">
        <el-tag v-if="debugMode && debugStore.connected" type="success" size="small">SSE 已订阅</el-tag>
        <el-tag v-else-if="debugMode" type="warning" size="small">SSE 连接中…</el-tag>
        <el-tag v-if="debugStore.sessionKey" size="small">
          key: {{ debugStore.sessionKey.key.slice(0, 12) }}…
        </el-tag>
        <div class="switch-container">
          <span class="switch-label">调试模式</span>
          <el-switch v-model="debugMode" inline-prompt />
        </div>
      </div>

      <div v-if="debugMode" class="debug-half">
        <div class="debug-header">
          <span class="section-label">HTTP 事件流</span>
          <span class="hint">按 correlation_id 分组; 展开卡片查看完整 body</span>
          <el-button size="small" @click="clearAll">清空调试日志</el-button>
        </div>

        <div class="groups">
          <div v-for="g in groups" :key="g.correlation_id" class="group">
            <div class="group-head">
              <code>{{ g.correlation_id }}</code>
              <span class="group-meta">{{ g.events.length }} 事件</span>
            </div>
            <div class="events">
              <div v-for="ev in g.events" :key="ev.id" class="event">
                <div class="event-head" @click="toggle(ev.id)">
                  <el-tag :type="directionTag(ev.direction)" size="small">
                    {{ directionLabel(ev.direction) }}
                  </el-tag>
                  <span class="e-time">{{ formatTs(ev.ts) }}</span>
                  <span v-if="ev.method" class="e-method">{{ ev.method }}</span>
                  <span class="e-url">{{ ev.url }}</span>
                  <span v-if="ev.port" class="e-port">:{{ ev.port }}</span>
                  <span v-if="ev.agent" class="e-agent">[{{ ev.agent }}]</span>
                  <span v-if="ev.status" class="e-status">{{ ev.status }}</span>
                  <span v-if="ev.duration_ms !== null" class="e-dur">
                    {{ ev.duration_ms.toFixed(0) }}ms
                  </span>
                  <span v-if="ev.key_note" class="e-key">key: {{ ev.key_note }}</span>
                  <el-icon class="e-caret">
                    <ArrowDown v-if="expanded.has(ev.id)" />
                    <ArrowRight v-else />
                  </el-icon>
                </div>

                <div v-if="expanded.has(ev.id)" class="event-body">
                  <div v-if="detailLoading.has(ev.id)" class="loading">加载中…</div>
                  <template v-else-if="details.get(ev.id)">
                    <div v-if="details.get(ev.id)!.stream_assembled" class="assembled">
                      <div class="section-label">汇总内容 ({{ details.get(ev.id)!.stream_chunks_count }} chunks)</div>
                      <pre class="pre">{{ details.get(ev.id)!.stream_assembled }}</pre>
                    </div>
                    <div v-if="details.get(ev.id)!.summary.headers" class="hdrs">
                      <div class="section-label">Headers</div>
                      <pre class="pre">{{ formatBody(details.get(ev.id)!.summary.headers) }}</pre>
                    </div>
                    <div class="body">
                      <div class="section-label">
                        Body ({{ details.get(ev.id)!.summary.body_full_size }} bytes<span
                          v-if="details.get(ev.id)!.summary.is_truncated"
                        >
                          · 已截断</span
                        >)
                      </div>
                      <pre class="pre">{{ formatBody(details.get(ev.id)!.body_full) }}</pre>
                    </div>
                  </template>
                  <div v-else class="preview">
                    <pre class="pre">{{ formatBody(ev.body_preview) }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="!groups.length" class="empty">还没有捕获到 HTTP 事件。发一条消息试试。</div>
        </div>
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

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: $space-3;
  flex-wrap: wrap;
  margin-bottom: 0;
}

.page-head-text { flex: 1 1 auto; min-width: 320px; }
.page-subtitle code {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  padding: 0 4px;
  background: var(--el-fill-color-light);
  border-radius: 3px;
}

.page-head-actions {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;

  .switch-container {
    display: flex;
    align-items: center;
    gap: $space-1;
    margin-left: auto;
    .switch-label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }
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

.err { margin-bottom: 0; }

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
  display: flex;
  flex-direction: column;
  gap: $space-2;
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
  display: flex;
  flex-direction: column;
  gap: $space-2;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
  background: var(--el-bg-color);
  padding: $space-3;
  overflow: hidden;
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

.debug-header {
  display: flex;
  align-items: center;
  gap: $space-3;
  .hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    flex: 1;
  }
}

.groups {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.group {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
}

.group-head {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
}

.group-meta { color: var(--el-text-color-secondary); }

.events { display: flex; flex-direction: column; }

.event { border-bottom: 1px solid var(--el-border-color-lighter); }
.event:last-child { border-bottom: 0; }

.event-head {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: 6px $space-3;
  cursor: pointer;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;

  &:hover { background: var(--el-fill-color); }

  .e-time { color: var(--el-text-color-secondary); }
  .e-method { font-weight: 600; }
  .e-url {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--el-text-color-primary);
  }
  .e-agent { color: var(--el-color-primary); }
  .e-status { color: var(--el-color-success); }
  .e-dur { color: var(--el-text-color-secondary); }
  .e-key { color: var(--el-text-color-secondary); font-size: 11px; }
  .e-caret { margin-left: auto; }
}

.event-body {
  padding: $space-2 $space-3;
  background: var(--el-bg-color);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.pre {
  margin: 0;
  padding: $space-2;
  background: var(--el-fill-color-light);
  border-radius: $radius-sm;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.loading { color: var(--el-text-color-secondary); }
.empty {
  color: var(--el-text-color-secondary);
  padding: $space-4;
  text-align: center;
}
</style>
