/**Conversation API: 跨前端对话流水管理. */

import type { ConversationTurnListResponse } from '@/types/api'
import { apiDelete, apiGet, apiPost } from './http'

export interface ConversationTurnListParams {
  page?: number
  page_size?: number
  role?: 'user' | 'assistant'
  source_frontend?: string
  actor_id?: string
  effective_user_id?: string
  space_id?: string
  origin?: 'current' | 'history_snapshot' | 'assistant' | 'legacy'
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

export async function listConversationTurns(
  params: ConversationTurnListParams = {},
): Promise<ConversationTurnListResponse> {
  const q = new URLSearchParams()
  if (params.page) q.set('page', String(params.page))
  if (params.page_size) q.set('page_size', String(params.page_size))
  if (params.role) q.set('role', params.role)
  if (params.source_frontend) q.set('source_frontend', params.source_frontend)
  if (params.actor_id) q.set('actor_id', params.actor_id)
  if (params.effective_user_id) q.set('effective_user_id', params.effective_user_id)
  if (params.space_id) q.set('space_id', params.space_id)
  if (params.origin) q.set('origin', params.origin)
  if (params.sort_by) q.set('sort_by', params.sort_by)
  if (params.sort_order) q.set('sort_order', params.sort_order)
  const qs = q.toString()
  return apiGet<ConversationTurnListResponse>(
    `/admin/conversation-turns${qs ? `?${qs}` : ''}`,
  )
}

export async function listConversationTurnSources(): Promise<{ items: string[] }> {
  return apiGet<{ items: string[] }>('/admin/conversation-turns/sources')
}

export async function deleteConversationTurn(turnId: number): Promise<void> {
  await apiDelete(`/admin/conversation-turns/${turnId}`)
}

export async function deleteConversationTurns(
  turnIds: number[],
): Promise<{ deleted: number }> {
  return apiPost('/admin/conversation-turns/batch-delete', { ids: turnIds })
}

export async function clearConversationTurns(
  since?: string,
): Promise<{ deleted: number }> {
  const qs = since ? `?since=${encodeURIComponent(since)}` : ''
  return apiDelete<{ deleted: number }>(`/admin/conversation-turns${qs}`)
}
