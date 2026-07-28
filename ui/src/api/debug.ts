/**Debug API: 调试面板事件、SSE 流. */

import type {
  DebugEventDetailResponse,
  DebugEventListResponse,
  DebugSessionKeyResponse,
  DebugStatusResponse,
} from '@/types/api'
import { API_BASE } from '@/utils/constants'
import { apiDelete, apiGet, apiPost, getToken } from './http'

export async function getDebugSessionKey(): Promise<DebugSessionKeyResponse> {
  return apiPost<DebugSessionKeyResponse>('/admin/debug/session-key')
}

export async function getDebugStatus(): Promise<DebugStatusResponse> {
  return apiGet<DebugStatusResponse>('/admin/debug/status')
}

export async function listDebugEvents(
  limit = 200,
): Promise<DebugEventListResponse> {
  return apiGet<DebugEventListResponse>(`/admin/debug/events?limit=${limit}`)
}

export async function getDebugEventDetail(
  eventId: string,
): Promise<DebugEventDetailResponse> {
  return apiGet<DebugEventDetailResponse>(
    `/admin/debug/events/${encodeURIComponent(eventId)}`,
  )
}

export async function clearDebugEvents(): Promise<void> {
  await apiDelete('/admin/debug/events')
}

/**打开 SSE 流 (EventSource 不支持自定义 header, 用 fetch + bearer auth).
 * 返回底层 Response, 调用方读取 response.body 逐行解析 SSE. */
export async function openDebugStream(signal: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(`${API_BASE}/admin/debug/events/stream`, {
    method: 'GET',
    headers,
    signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE open failed: HTTP ${resp.status}`)
  }
  return resp
}
