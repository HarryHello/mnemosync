/**API Keys API: 管理面板 API Key. */

import type {
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  ApiKeyListResponse,
} from '@/types/api'
import { apiDelete, apiGet, apiPost } from './http'

export async function listApiKeys(): Promise<ApiKeyListResponse> {
  return apiGet<ApiKeyListResponse>('/api-keys')
}

export async function createApiKey(
  data: ApiKeyCreateRequest,
): Promise<ApiKeyCreateResponse> {
  return apiPost<ApiKeyCreateResponse>('/api-keys', data)
}

export async function deleteApiKey(keyId: string): Promise<void> {
  await apiDelete(`/api-keys/${keyId}`)
}
