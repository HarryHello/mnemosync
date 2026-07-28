/**Core API: 健康检查、仪表盘统计、HTTP 日志. */

import type { HealthResponse, HttpLog, HttpLogListResponse } from '@/types/api'
import { API_BASE } from '@/utils/constants'
import { apiDelete, apiGet, request } from './http'

// ============================================================================
// Health
// ============================================================================

export async function healthCheck(): Promise<HealthResponse> {
  return apiGet<HealthResponse>('/admin/health')
}

export interface DashboardStats {
  api_keys: number
  memories: number
  logs: number
  prompts_total: number
  prompts_overridden: number
  health: HealthResponse
}

export async function getDashboardStats(): Promise<DashboardStats> {
  return apiGet<DashboardStats>('/admin/stats')
}

// ============================================================================
// HTTP Logs
// ============================================================================

export interface LogListParams {
  page?: number
  page_size?: number
  method?: string
  path?: string
  status?: number
  since?: string
  until?: string
}

export async function listLogs(
  params: LogListParams = {},
): Promise<HttpLogListResponse> {
  const searchParams = new URLSearchParams()
  if (params.page) searchParams.set('page', String(params.page))
  if (params.page_size) searchParams.set('page_size', String(params.page_size))
  if (params.method) searchParams.set('method', params.method)
  if (params.path) searchParams.set('path', params.path)
  if (params.status) searchParams.set('status', String(params.status))
  if (params.since) searchParams.set('since', params.since)
  if (params.until) searchParams.set('until', params.until)

  const query = searchParams.toString()
  return request<HttpLogListResponse>(
    `${API_BASE}/admin/logs${query ? `?${query}` : ''}`,
  )
}

export async function getLog(logId: number): Promise<HttpLog> {
  return apiGet<HttpLog>(`/admin/logs/${logId}`)
}

export async function clearLogs(): Promise<void> {
  await apiDelete('/admin/logs')
}
