/**Core API: 健康检查、仪表盘统计、HTTP 日志. */

import type { HealthResponse, HttpLog, HttpLogListResponse } from '@/types/api'
import { API_BASE, buildQuery } from '@/utils/constants'
import { apiDelete, apiGet, apiPost, apiPut, request } from './http'

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

export interface ReleaseInfo {
  version: string
  description: string
  published_at: string
  is_prerelease: boolean
  url: string
}

export interface VersionsResult {
  current_version: string
  releases: ReleaseInfo[]
}

export async function listVersions(): Promise<VersionsResult> {
  return apiGet<VersionsResult>('/admin/versions')
}

export interface UpgradeResult {
  success: boolean
  message: string
}

export async function upgradeService(version?: string): Promise<UpgradeResult> {
  return apiPost<UpgradeResult>('/admin/upgrade', version ? { version } : {})
}

// ============================================================================
// Prompt Cleaning Cache (v0.4.1)
// ============================================================================

export interface PromptCacheEntryDto {
  frontend: string
  module_hash: string
  module_title: string
  module_text: string
  clean_prompt: string
  skipped: boolean
  created_at: string
  updated_at: string
}

export interface PromptCacheListDto {
  items: PromptCacheEntryDto[]
  total: number
}

export async function listPromptCleanCache(frontend?: string, page = 1, pageSize = 50): Promise<PromptCacheListDto> {
  const params = new URLSearchParams()
  if (frontend) params.set('frontend', frontend)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return apiGet<PromptCacheListDto>(`/admin/prompt-cleaning/cache?${params}`)
}

export async function deletePromptCleanCache(frontend: string, hash: string): Promise<{ success: boolean }> {
  return apiDelete(`/admin/prompt-cleaning/cache/${hash}?frontend=${encodeURIComponent(frontend)}`)
}

export async function updatePromptCleanCache(frontend: string, hash: string, cleanPrompt: string): Promise<{ success: boolean }> {
  return apiPut(`/admin/prompt-cleaning/cache/${hash}?frontend=${encodeURIComponent(frontend)}`, { clean_prompt: cleanPrompt })
}

export async function reCleanPromptCache(frontend: string, hash: string): Promise<{ success: boolean; clean_prompt: string }> {
  return apiPost(`/admin/prompt-cleaning/cache/${hash}/re-clean?frontend=${encodeURIComponent(frontend)}`)
}

export async function clearPromptCleanCache(): Promise<{ success: boolean; deleted: number }> {
  return apiDelete('/admin/prompt-cleaning/cache')
}

export interface PromptCleanSettingDto {
  frontend: string
  module_title: string
  skip: boolean
}

export async function listPromptCleanSettings(): Promise<{ items: PromptCleanSettingDto[]; total: number }> {
  return apiGet('/admin/prompt-cleaning/settings')
}

export async function setPromptCleanSetting(frontend: string, moduleTitle: string, skip: boolean): Promise<{ success: boolean }> {
  return apiPut('/admin/prompt-cleaning/settings', { frontend, module_title: moduleTitle, skip })
}
