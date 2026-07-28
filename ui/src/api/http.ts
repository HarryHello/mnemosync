/**HTTP 请求基础封装: token 管理 + 统一错误处理. */

import { API_BASE, LOCAL_STORAGE_KEYS } from '@/utils/constants'

// ============================================================================
// Token 管理
// ============================================================================

let authToken: string | null = localStorage.getItem(LOCAL_STORAGE_KEYS.token)

export function setToken(token: string | null) {
  authToken = token
  if (token) {
    localStorage.setItem(LOCAL_STORAGE_KEYS.token, token)
  } else {
    localStorage.removeItem(LOCAL_STORAGE_KEYS.token)
  }
}

export function getToken(): string | null {
  return authToken
}

// ============================================================================
// 请求封装
// ============================================================================

/**面板 API 请求封装 (自动注入 Authorization + Content-Type). */
export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
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

  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    throw new Error(
      `API 未返回 JSON (${response.status} ${contentType || '未知类型'}); 检查后端版本是否已重启`,
    )
  }
  return response.json()
}

/**便捷方法: 拼接 /panel 前缀的 GET 请求. */
export function apiGet<T>(path: string): Promise<T> {
  return request<T>(`${API_BASE}${path}`)
}

/**便捷方法: 拼接 /panel 前缀的 POST 请求. */
export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(`${API_BASE}${path}`, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

/**便捷方法: 拼接 /panel 前缀的 PUT 请求. */
export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(`${API_BASE}${path}`, {
    method: 'PUT',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

/**便捷方法: 拼接 /panel 前缀的 PATCH 请求. */
export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(`${API_BASE}${path}`, {
    method: 'PATCH',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

/**便捷方法: 拼接 /panel 前缀的 DELETE 请求. */
export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(`${API_BASE}${path}`, { method: 'DELETE' })
}

export { API_BASE }
