/** API 服务函数 */

import type {
  LoginRequest,
  LoginResponse,
  UserInfoResponse,
  ChangePasswordRequest,
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  ApiKeyListResponse,
  HttpLogListResponse,
  HttpLog,
  MemoryListResponse,
  Memory,
  Relationship,
  HealthResponse,
  ChatCompletionRequest,
  ChatCompletionResponse,
  PromptSummary,
  PromptDetail,
  PromptValidateResponse,
  PromptHistoryResponse,
  UpstreamService,
  UpstreamServiceCreateBody,
  UpstreamServiceUpdateBody,
  UpstreamAvailableModels,
  RoleBindingItem,
  RoleBindingListResponse,
  RoleBindingAddBody,
  ProbeDimensionBody,
  ProbeDimensionResponse,
  ReindexStartBody,
  ReindexStatusResponse,
  PruneStartBody,
  PruneResponse,
  DebugSessionKeyResponse,
  DebugEventListResponse,
  DebugEventDetailResponse,
  DebugStatusResponse,
} from '@/types/api'

// ============================================================================
// 配置
// ============================================================================

const API_BASE = '/panel'
const CHAT_BASE = '/v1'

// ============================================================================
// Token 管理
// ============================================================================

let authToken: string | null = localStorage.getItem('mnemosync_token')

export function setToken(token: string | null) {
  authToken = token
  if (token) {
    localStorage.setItem('mnemosync_token', token)
  } else {
    localStorage.removeItem('mnemosync_token')
  }
}

export function getToken(): string | null {
  return authToken
}

// ============================================================================
// 请求封装
// ============================================================================

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json()
}

// ============================================================================
// Auth API
// ============================================================================

export async function login(data: LoginRequest): Promise<LoginResponse> {
  const result = await request<LoginResponse>(`${API_BASE}/auth/login`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
  setToken(result.access_token)
  return result
}

export async function logout(): Promise<void> {
  await request(`${API_BASE}/auth/logout`, { method: 'POST' })
  setToken(null)
}

export async function getCurrentUser(): Promise<UserInfoResponse> {
  return request<UserInfoResponse>(`${API_BASE}/auth/me`)
}

export async function changePassword(data: ChangePasswordRequest): Promise<{ success: boolean }> {
  return request(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ============================================================================
// API Keys API
// ============================================================================

export async function listApiKeys(): Promise<ApiKeyListResponse> {
  return request<ApiKeyListResponse>(`${API_BASE}/api-keys`)
}

export async function createApiKey(data: ApiKeyCreateRequest): Promise<ApiKeyCreateResponse> {
  return request<ApiKeyCreateResponse>(`${API_BASE}/api-keys`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function deleteApiKey(keyId: string): Promise<void> {
  await request(`${API_BASE}/api-keys/${keyId}`, { method: 'DELETE' })
}

// ============================================================================
// Admin API - Logs
// ============================================================================

export interface LogListParams {
  page?: number
  page_size?: number
  method?: string
  path?: string
  status?: number
}

export async function listLogs(params: LogListParams = {}): Promise<HttpLogListResponse> {
  const searchParams = new URLSearchParams()
  if (params.page) searchParams.set('page', String(params.page))
  if (params.page_size) searchParams.set('page_size', String(params.page_size))
  if (params.method) searchParams.set('method', params.method)
  if (params.path) searchParams.set('path', params.path)
  if (params.status) searchParams.set('status', String(params.status))

  const query = searchParams.toString()
  return request<HttpLogListResponse>(`${API_BASE}/admin/logs${query ? `?${query}` : ''}`)
}

export async function getLog(logId: number): Promise<HttpLog> {
  return request<HttpLog>(`${API_BASE}/admin/logs/${logId}`)
}

export async function clearLogs(): Promise<void> {
  await request(`${API_BASE}/admin/logs`, { method: 'DELETE' })
}

// ============================================================================
// Admin API - Memories
// ============================================================================

export async function listMemories(sourceUser: string = 'default'): Promise<MemoryListResponse> {
  return request<MemoryListResponse>(`${API_BASE}/admin/memories?source_user=${sourceUser}`)
}

export async function getMemory(memoryId: string): Promise<Memory> {
  return request<Memory>(`${API_BASE}/admin/memories/${memoryId}`)
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await request(`${API_BASE}/admin/memories/${memoryId}`, { method: 'DELETE' })
}

// ============================================================================
// Admin API - Relationship
// ============================================================================

export async function getRelationship(userId: string = 'default'): Promise<Relationship> {
  return request<Relationship>(`${API_BASE}/admin/relationship?user_id=${userId}`)
}

// ============================================================================
// Admin API - Health
// ============================================================================

export async function healthCheck(): Promise<HealthResponse> {
  return request<HealthResponse>(`${API_BASE}/admin/health`)
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
  return request<DashboardStats>(`${API_BASE}/admin/stats`)
}

// ============================================================================
// Admin API - Prompts
// ============================================================================

export async function listPrompts(): Promise<PromptSummary[]> {
  return request<PromptSummary[]>(`${API_BASE}/admin/prompts`)
}

export async function getPrompt(name: string): Promise<PromptDetail> {
  return request<PromptDetail>(`${API_BASE}/admin/prompts/${encodeURIComponent(name)}`)
}

export async function putPrompt(name: string, content: string): Promise<PromptSummary> {
  return request<PromptSummary>(`${API_BASE}/admin/prompts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  })
}

export async function resetPrompt(name: string): Promise<PromptSummary> {
  return request<PromptSummary>(`${API_BASE}/admin/prompts/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

export async function validatePrompt(
  name: string,
  content: string,
): Promise<PromptValidateResponse> {
  return request<PromptValidateResponse>(
    `${API_BASE}/admin/prompts/${encodeURIComponent(name)}:validate`,
    {
      method: 'POST',
      body: JSON.stringify({ content }),
    },
  )
}

export async function getPromptHistory(name: string): Promise<PromptHistoryResponse> {
  return request<PromptHistoryResponse>(
    `${API_BASE}/admin/prompts/${encodeURIComponent(name)}/history`,
  )
}

// ============================================================================
// Admin API - Upstream LLM Services
// ============================================================================

export async function listUpstreamServices(): Promise<UpstreamService[]> {
  return request<UpstreamService[]>(`${API_BASE}/admin/upstream/services`)
}

export async function createUpstreamService(
  body: UpstreamServiceCreateBody,
): Promise<UpstreamService> {
  return request<UpstreamService>(`${API_BASE}/admin/upstream/services`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function updateUpstreamService(
  id: string,
  body: UpstreamServiceUpdateBody,
): Promise<UpstreamService> {
  return request<UpstreamService>(
    `${API_BASE}/admin/upstream/services/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
  )
}

export async function deleteUpstreamService(id: string): Promise<void> {
  await request(`${API_BASE}/admin/upstream/services/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export async function listUpstreamAvailableModels(
  id: string,
): Promise<UpstreamAvailableModels> {
  return request<UpstreamAvailableModels>(
    `${API_BASE}/admin/upstream/services/${encodeURIComponent(id)}/available-models`,
  )
}

// ============================================================================
// Admin API - Role Bindings (v0.2.3)
// ============================================================================

export async function listModelBindings(role?: string): Promise<RoleBindingListResponse> {
  const q = role ? `?role=${encodeURIComponent(role)}` : ''
  return request<RoleBindingListResponse>(`${API_BASE}/admin/model-bindings${q}`)
}

export async function addModelBinding(body: RoleBindingAddBody): Promise<RoleBindingItem> {
  return request<RoleBindingItem>(`${API_BASE}/admin/model-bindings`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function deleteModelBinding(role: string, priority: number): Promise<void> {
  await request(
    `${API_BASE}/admin/model-bindings/${encodeURIComponent(role)}/${priority}`,
    { method: 'DELETE' },
  )
}

export async function reorderModelBindings(
  role: string,
  order: [string, string][],
): Promise<RoleBindingListResponse> {
  return request<RoleBindingListResponse>(
    `${API_BASE}/admin/model-bindings/${encodeURIComponent(role)}/reorder`,
    {
      method: 'PUT',
      body: JSON.stringify({ order }),
    },
  )
}

export async function probeEmbeddingDimension(
  body: ProbeDimensionBody,
): Promise<ProbeDimensionResponse> {
  return request<ProbeDimensionResponse>(
    `${API_BASE}/admin/model-bindings/probe-dimension`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

// ============================================================================
// Admin API - Memory Reindex + Prune (v0.2.4)
// ============================================================================

export async function startMemoryReindex(
  body: ReindexStartBody = {},
): Promise<ReindexStatusResponse> {
  return request<ReindexStatusResponse>(`${API_BASE}/admin/memory/reindex`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getMemoryReindexStatus(): Promise<ReindexStatusResponse> {
  return request<ReindexStatusResponse>(`${API_BASE}/admin/memory/reindex/status`)
}

export async function pruneMemories(body: PruneStartBody = {}): Promise<PruneResponse> {
  return request<PruneResponse>(`${API_BASE}/admin/memory/prune`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ============================================================================
// Admin API - Debug Panel (v0.2.5)
// ============================================================================

export async function getDebugSessionKey(): Promise<DebugSessionKeyResponse> {
  return request<DebugSessionKeyResponse>(`${API_BASE}/admin/debug/session-key`, {
    method: 'POST',
  })
}

export async function getDebugStatus(): Promise<DebugStatusResponse> {
  return request<DebugStatusResponse>(`${API_BASE}/admin/debug/status`)
}

export async function listDebugEvents(limit = 200): Promise<DebugEventListResponse> {
  return request<DebugEventListResponse>(`${API_BASE}/admin/debug/events?limit=${limit}`)
}

export async function getDebugEventDetail(eventId: string): Promise<DebugEventDetailResponse> {
  return request<DebugEventDetailResponse>(
    `${API_BASE}/admin/debug/events/${encodeURIComponent(eventId)}`,
  )
}

export async function clearDebugEvents(): Promise<void> {
  await request(`${API_BASE}/admin/debug/events`, { method: 'DELETE' })
}

/** Open SSE stream with bearer auth via fetch (EventSource 不支持自定义 header).
 *  返回底层 Response, 调用方读取 response.body 逐行解析 SSE. */
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

// ============================================================================
// Models (OpenAI Compatible)
// ============================================================================

export async function listV1Models(apiKey: string): Promise<{ data: Array<{ id: string }> }> {
  const resp = await fetch(`${CHAT_BASE}/models`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

// ============================================================================
// Chat API (OpenAI Compatible)
// ============================================================================

export async function chatCompletion(data: ChatCompletionRequest): Promise<ChatCompletionResponse> {
  const apiKey = localStorage.getItem('mnemosync_api_key')
  if (!apiKey) {
    throw new Error('No API key configured')
  }

  return request<ChatCompletionResponse>(`${CHAT_BASE}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: 'mnemosync-any',
      ...data,
    }),
  })
}
