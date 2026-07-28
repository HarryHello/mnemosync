/**Upstream API: LLM 服务商 + 模型绑定 + 维度探测. */

import type {
  ProbeDimensionBody,
  ProbeDimensionResponse,
  RoleBindingAddBody,
  RoleBindingItem,
  RoleBindingListResponse,
  RoleBindingUpdateBody,
  UpstreamAvailableModels,
  UpstreamService,
  UpstreamServiceCreateBody,
  UpstreamServiceUpdateBody,
} from '@/types/api'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from './http'

// ============================================================================
// Services
// ============================================================================

export async function listUpstreamServices(): Promise<UpstreamService[]> {
  return apiGet<UpstreamService[]>('/admin/upstream/services')
}

export async function createUpstreamService(
  body: UpstreamServiceCreateBody,
): Promise<UpstreamService> {
  return apiPost<UpstreamService>('/admin/upstream/services', body)
}

export async function updateUpstreamService(
  id: string,
  body: UpstreamServiceUpdateBody,
): Promise<UpstreamService> {
  return apiPatch<UpstreamService>(
    `/admin/upstream/services/${encodeURIComponent(id)}`,
    body,
  )
}

export async function deleteUpstreamService(id: string): Promise<void> {
  await apiDelete(`/admin/upstream/services/${encodeURIComponent(id)}`)
}

export async function listUpstreamAvailableModels(
  id: string,
): Promise<UpstreamAvailableModels> {
  return apiGet<UpstreamAvailableModels>(
    `/admin/upstream/services/${encodeURIComponent(id)}/available-models`,
  )
}

// ============================================================================
// Role Bindings
// ============================================================================

export async function listModelBindings(
  role?: string,
): Promise<RoleBindingListResponse> {
  const q = role ? `?role=${encodeURIComponent(role)}` : ''
  return apiGet<RoleBindingListResponse>(`/admin/model-bindings${q}`)
}

export async function addModelBinding(
  body: RoleBindingAddBody,
): Promise<RoleBindingItem> {
  return apiPost<RoleBindingItem>('/admin/model-bindings', body)
}

export async function deleteModelBinding(
  role: string,
  priority: number,
): Promise<void> {
  await apiDelete(
    `/admin/model-bindings/${encodeURIComponent(role)}/${priority}`,
  )
}

export async function updateModelBinding(
  role: string,
  priority: number,
  body: RoleBindingUpdateBody,
): Promise<RoleBindingItem> {
  return apiPatch<RoleBindingItem>(
    `/admin/model-bindings/${encodeURIComponent(role)}/${priority}`,
    body,
  )
}

export async function reorderModelBindings(
  role: string,
  order: [string, string][],
): Promise<RoleBindingListResponse> {
  return apiPut<RoleBindingListResponse>(
    `/admin/model-bindings/${encodeURIComponent(role)}/reorder`,
    { order },
  )
}

export async function probeEmbeddingDimension(
  body: ProbeDimensionBody,
): Promise<ProbeDimensionResponse> {
  return apiPost<ProbeDimensionResponse>(
    '/admin/model-bindings/probe-dimension',
    body,
  )
}
