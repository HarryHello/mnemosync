/**Prompts API: Agent 提示词覆盖管理. */

import type {
  PromptDetail,
  PromptHistoryResponse,
  PromptSummary,
  PromptValidateResponse,
} from '@/types/api'
import { apiDelete, apiGet, apiPost, apiPut } from './http'

export async function listPrompts(): Promise<PromptSummary[]> {
  return apiGet<PromptSummary[]>('/admin/prompts')
}

export async function getPrompt(name: string): Promise<PromptDetail> {
  return apiGet<PromptDetail>(`/admin/prompts/${encodeURIComponent(name)}`)
}

export async function putPrompt(
  name: string,
  content: string,
): Promise<PromptSummary> {
  return apiPut<PromptSummary>(`/admin/prompts/${encodeURIComponent(name)}`, {
    content,
  })
}

export async function resetPrompt(name: string): Promise<PromptSummary> {
  return apiDelete<PromptSummary>(`/admin/prompts/${encodeURIComponent(name)}`)
}

export async function validatePrompt(
  name: string,
  content: string,
): Promise<PromptValidateResponse> {
  return apiPost<PromptValidateResponse>(
    `/admin/prompts/${encodeURIComponent(name)}:validate`,
    { content },
  )
}

export async function getPromptHistory(
  name: string,
): Promise<PromptHistoryResponse> {
  return apiGet<PromptHistoryResponse>(
    `/admin/prompts/${encodeURIComponent(name)}/history`,
  )
}

// ── 情绪矩阵 ────────────────────────────────────────────────

export interface MoodMatrixCell {
  id: string
  text: string
  overridden: boolean
}

export interface MoodMatrixResponse {
  favor_tiers: { id: string; label: string }[]
  mood_labels: string[]
  cells: MoodMatrixCell[]
}

export async function getMoodMatrix(): Promise<MoodMatrixResponse> {
  return apiGet<MoodMatrixResponse>('/admin/mood-matrix')
}

export async function putMoodMatrixCell(cellId: string, text: string): Promise<void> {
  await apiPut(`/admin/mood-matrix/${encodeURIComponent(cellId)}`, { text })
}

export async function deleteMoodMatrixCell(cellId: string): Promise<void> {
  await apiDelete(`/admin/mood-matrix/${encodeURIComponent(cellId)}`)
}
