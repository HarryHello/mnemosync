/**Core API: 健康检查、仪表盘统计、HTTP 日志. */

import type { HealthResponse, HttpLog, HttpLogListResponse } from '@/types/api'
import { API_BASE, buildQuery } from '@/utils/constants'
import { apiDelete, apiGet, apiPost, request } from './http'

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
  return request<HttpLogListResponse>(
    `${API_BASE}/admin/logs${buildQuery(params)}`,
  )
}

export async function getLog(logId: number): Promise<HttpLog> {
  return apiGet<HttpLog>(`/admin/logs/${logId}`)
}

export async function clearLogs(): Promise<void> {
  await apiDelete('/admin/logs')
}

// ============================================================================
// Restart
// ============================================================================

export interface RestartResponse {
  success: boolean
  message: string
}

export async function restartService(): Promise<RestartResponse> {
  return apiPost<RestartResponse>('/admin/restart')
}

// ============================================================================
// Update Check & Upgrade
// ============================================================================

export interface UpdateCheckResult {
  update_available: boolean
  latest_version?: string
  current_version?: string
  url?: string
}

export async function checkUpdate(): Promise<UpdateCheckResult> {
  return apiGet<UpdateCheckResult>('/admin/check-update')
}

export interface UpgradeResult {
  success: boolean
  message: string
}

export async function upgradeService(): Promise<UpgradeResult> {
  return apiPost<UpgradeResult>('/admin/upgrade')
}
