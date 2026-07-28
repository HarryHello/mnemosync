/** 调试面板事件流 Pinia store.
 *
 * 设计要点:
 * - 全局单例, 在 App.vue mount 时挂载订阅. 路由切换不断线, 避免后端 grace 定时器
 *   误清 panel-debug key.
 * - 打开调试页时 activate(): 拉 session-key + 拉最近事件 + 开 SSE.
 * - 关闭 (用户离开 debug 页 + 无引用) 时 deactivate(): abort fetch, 后端订阅数
 *   掉到 0 后 30s grace 到期清 key.
 * - 事件按 correlation_id 分组供 UI 展示; 每次 SSE 推送去重 (id 集合).
 */

import { defineStore } from 'pinia'
import { ref, computed, shallowRef } from 'vue'
import type { DebugEventSummary, DebugSessionKeyResponse } from '@/types/api'
import {
  getDebugSessionKey,
  listDebugEvents,
  clearDebugEvents,
  openDebugStream,
} from '@/api/client'
import { parseSSEBuffer } from '@/utils/sse'

const CLIENT_MAX_EVENTS = 500

export const useDebugStore = defineStore('debug', () => {
  const active = ref(false)
  const connected = ref(false)
  const sessionKey = shallowRef<DebugSessionKeyResponse | null>(null)
  const events = ref<DebugEventSummary[]>([])
  const seenIds = new Set<string>()
  const error = ref<string | null>(null)
  let abortCtl: AbortController | null = null
  let reconnectTimer: number | null = null

  const eventsByCorrelation = computed(() => {
    const map = new Map<string, DebugEventSummary[]>()
    for (const e of events.value) {
      const arr = map.get(e.correlation_id) ?? []
      arr.push(e)
      map.set(e.correlation_id, arr)
    }
    // 每组内按 ts 升序
    for (const arr of map.values()) arr.sort((a, b) => a.ts - b.ts)
    return map
  })

  function ingest(ev: DebugEventSummary) {
    if (seenIds.has(ev.id)) return
    seenIds.add(ev.id)
    events.value.push(ev)
    if (events.value.length > CLIENT_MAX_EVENTS) {
      const dropped = events.value.splice(0, events.value.length - CLIENT_MAX_EVENTS)
      for (const d of dropped) seenIds.delete(d.id)
    }
  }

  async function fetchBacklog() {
    try {
      const resp = await listDebugEvents(200)
      // 反向: 后端 list_recent 返回按 ts 升序即可; 逐个 ingest 保序去重
      for (const e of resp.items) ingest(e)
    } catch (e) {
      error.value = `拉取历史事件失败: ${(e as Error).message}`
    }
  }

  async function ensureSessionKey() {
    if (sessionKey.value) return sessionKey.value
    sessionKey.value = await getDebugSessionKey()
    return sessionKey.value
  }

  /** 只拿 session key, 不订阅 SSE. 供聊天页在"未开调试"时使用. */
  async function ensureKey() {
    try {
      await ensureSessionKey()
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  async function runStream(signal: AbortSignal) {
    const resp = await openDebugStream(signal)
    connected.value = true
    error.value = null
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder('utf-8')
    let buf = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const { events, remainder } = parseSSEBuffer(buf)
      buf = remainder
      for (const ev of events) {
        if (ev.event === 'ready') continue
        try {
          const obj = JSON.parse(ev.data) as DebugEventSummary
          ingest(obj)
        } catch {
          // 忽略非 JSON 帧
        }
      }
    }
    connected.value = false
  }

  async function connectLoop() {
    while (active.value) {
      abortCtl = new AbortController()
      try {
        await runStream(abortCtl.signal)
      } catch (e) {
        connected.value = false
        if (!active.value) return
        // 认证失败 (401) 不重试 — 停止重连循环, 避免持续打 401 日志
        if ((e as Error).message?.includes('HTTP 401')) {
          error.value = '调试会话认证已失效，请刷新页面重新获取 Key'
          deactivate()
          return
        }
        error.value = `SSE 断开: ${(e as Error).message}`
      }
      if (!active.value) return
      // 指数退避简化为固定 2s
      await new Promise<void>((resolve) => {
        reconnectTimer = window.setTimeout(() => resolve(), 2000)
      })
    }
  }

  async function activate() {
    if (active.value) return
    active.value = true
    error.value = null
    try {
      await ensureSessionKey()
      await fetchBacklog()
    } catch (e) {
      error.value = (e as Error).message
    }
    void connectLoop()
  }

  function deactivate() {
    if (!active.value) return
    active.value = false
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (abortCtl) {
      abortCtl.abort()
      abortCtl = null
    }
    connected.value = false
  }

  async function clearAll() {
    await clearDebugEvents()
    events.value = []
    seenIds.clear()
  }

  return {
    active,
    connected,
    sessionKey,
    events,
    eventsByCorrelation,
    error,
    activate,
    deactivate,
    ensureKey,
    clearAll,
  }
})
