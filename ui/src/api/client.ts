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
} from '@/types/api'

// ============================================================================
// 配置
// ============================================================================

const API_BASE = '/api/v1'
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
