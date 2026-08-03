/**Backend API: 后端进程状态查询与启停管理. */

import type { BackendStatusResponse, BackendActionResponse } from '@/types/api'
import { apiGet, apiPost } from './http'

export async function getBackendStatus(): Promise<BackendStatusResponse> {
  return apiGet<BackendStatusResponse>('/admin/backend/status')
}

export async function startBackend(): Promise<BackendActionResponse> {
  return apiPost<BackendActionResponse>('/admin/backend/start')
}

export async function stopBackend(): Promise<BackendActionResponse> {
  return apiPost<BackendActionResponse>('/admin/backend/stop')
}

export async function restartBackend(): Promise<BackendActionResponse> {
  return apiPost<BackendActionResponse>('/admin/backend/restart')
}