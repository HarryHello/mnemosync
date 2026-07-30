/**Conversation API: 跨前端对话流水管理. */

import type { ConversationTurnListResponse, InteractionListResponse } from '@/types/api'
import { buildQuery } from '@/utils/constants'
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
  interaction_id?: string
  event_type?: string  // message | tool_call | tool_result
  tool_name?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

export async function listConversationTurns(
  params: ConversationTurnListParams = {},
): Promise<ConversationTurnListResponse> {
  return apiGet<ConversationTurnListResponse>(
    `/admin/conversation-turns${buildQuery(params)}`,
  )
}

export async function listInteractions(
  limit: number = 20,
  spaceId?: string,
): Promise<InteractionListResponse> {
  return apiGet<InteractionListResponse>(
    `/admin/conversation-turns/interactions${buildQuery({ limit, space_id: spaceId })}`,
  )
}

export async function listConversationTurnSources(): Promise<{ items: string[] }> {
  return apiGet<{ items: string[] }>('/admin/conversation-turns/sources')
}

export interface SpeakerItem {
  effective_user_id: string
  display_name: string
  actor_id: string
}

export async function listConversationTurnSpeakers(): Promise<{ items: SpeakerItem[] }> {
  return apiGet<{ items: SpeakerItem[] }>('/admin/conversation-turns/speakers')
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
