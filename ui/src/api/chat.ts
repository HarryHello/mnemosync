/**Chat API: OpenAI 兼容的 /v1 端点. */

import type { ChatCompletionRequest, ChatCompletionResponse } from '@/types/api'
import { CHAT_BASE, LOCAL_STORAGE_KEYS, VIRTUAL_MODEL_ANY } from '@/utils/constants'
import { request } from './http'

export async function listV1Models(
  apiKey: string,
): Promise<{ data: Array<{ id: string }> }> {
  const resp = await fetch(`${CHAT_BASE}/models`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export async function chatCompletion(
  data: ChatCompletionRequest,
): Promise<ChatCompletionResponse> {
  const apiKey = localStorage.getItem(LOCAL_STORAGE_KEYS.debugApiKey)
  if (!apiKey) {
    throw new Error('No API key configured')
  }

  return request<ChatCompletionResponse>(`${CHAT_BASE}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: VIRTUAL_MODEL_ANY,
      ...data,
    }),
  })
}

/**流式聊天 / 调试面板用: 接受外部 API Key, 返回原始 Response,
 *  调用方自行处理流式读取或 JSON 解析. */
export async function chatCompletionRaw(
  apiKey: string,
  body: Record<string, unknown>,
): Promise<Response> {
  const resp = await fetch(`${CHAT_BASE}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: VIRTUAL_MODEL_ANY,
      ...body,
    }),
  })
  return resp
}
