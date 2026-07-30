/**Memories API: 记忆 CRUD、关系查询/更新/审计、reindex、prune. */

import type {
  Memory,
  MemoryListResponse,
  PruneResponse,
  PruneStartBody,
  ReindexStartBody,
  ReindexStatusResponse,
  Relationship,
  RelationshipAuditListResponse,
  RelationshipListResponse,
  RelationshipUpdateBody,
} from '@/types/api'
import { API_BASE, buildQuery } from '@/utils/constants'
import { apiDelete, apiGet, apiPost, request } from './http'

// ============================================================================
// Memories CRUD
// ============================================================================

export interface MemoryListParams {
  source_user?: string
  page?: number
  page_size?: number
  memory_type?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  before?: string
  after?: string
}

export async function listMemories(
  params: MemoryListParams = {},
): Promise<MemoryListResponse> {
  return request<MemoryListResponse>(
    `${API_BASE}/admin/memories${buildQuery(params)}`,
  )
}

export async function getMemory(memoryId: string): Promise<Memory> {
  return apiGet<Memory>(`/admin/memories/${memoryId}`)
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await apiDelete(`/admin/memories/${memoryId}`)
}

export async function listMemorySources(): Promise<{ items: string[] }> {
  return apiGet<{ items: string[] }>('/admin/memories/sources')
}

export interface BatchDeleteMemoriesParams {
  source_user: string
  memory_type?: 'permanent' | 'normal'
  before?: string
}

export async function deleteMemoriesBatch(
  params: BatchDeleteMemoriesParams,
): Promise<{ success: boolean; deleted: number }> {
  return request<{ success: boolean; deleted: number }>(
    `${API_BASE}/admin/memories${buildQuery(params)}`,
    { method: 'DELETE' },
  )
}

// ============================================================================
// Relationship
// ============================================================================

export async function getRelationship(userId: string = 'default'): Promise<Relationship> {
  return apiGet<Relationship>(`/admin/relationship?user_id=${userId}`)
}

export async function updateRelationship(
  body: RelationshipUpdateBody,
): Promise<Relationship> {
  return request<Relationship>(`${API_BASE}/admin/relationship`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function getRelationshipAudit(
  userId: string = 'default',
  limit: number = 20,
): Promise<RelationshipAuditListResponse> {
  return apiGet<RelationshipAuditListResponse>(
    `/admin/relationship/audit?user_id=${userId}&limit=${limit}`,
  )
}

export interface ListRelationshipsParams {
  page?: number
  page_size?: number
  sort_by?: string
  sort_order?: string
}

export async function listRelationships(
  params: ListRelationshipsParams = {},
): Promise<RelationshipListResponse> {
  return request<RelationshipListResponse>(
    `${API_BASE}/admin/relationships${buildQuery(params)}`,
  )
}

// ============================================================================
// Memory reindex + prune
// ============================================================================

export async function startMemoryReindex(
  body: ReindexStartBody = {},
): Promise<ReindexStatusResponse> {
  return apiPost<ReindexStatusResponse>('/admin/memory/reindex', body)
}

export async function getMemoryReindexStatus(): Promise<ReindexStatusResponse> {
  return apiGet<ReindexStatusResponse>('/admin/memory/reindex/status')
}

export async function pruneMemories(
  body: PruneStartBody = {},
): Promise<PruneResponse> {
  return apiPost<PruneResponse>('/admin/memory/prune', body)
}
