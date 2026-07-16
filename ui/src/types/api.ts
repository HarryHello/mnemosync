/** API 类型定义 */

// ============================================================================
// Auth
// ============================================================================

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  must_change_password: boolean
  username: string
}

export interface UserInfo {
  id: string
  username: string
  must_change_password: boolean
  created_at: string
  last_login_at: string | null
}

export interface UserInfoResponse {
  user: UserInfo
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}

// ============================================================================
// API Keys
// ============================================================================

export interface ApiKeyCreateRequest {
  note: string
}

export interface ApiKeyCreateResponse {
  id: string
  key: string
  key_prefix: string
  note: string
  created_at: string
}

export interface ApiKeyInfo {
  id: string
  key_prefix: string
  note: string
  created_at: string
  last_used_at: string | null
  is_active: boolean
}

export interface ApiKeyListResponse {
  items: ApiKeyInfo[]
}

// ============================================================================
// HTTP Logs
// ============================================================================

export interface HttpLog {
  id: number
  method: string
  path: string
  query_params: string | null
  request_headers: Record<string, string> | null
  request_body: unknown
  response_status: number | null
  response_body: unknown
  duration_ms: number | null
  client_ip: string | null
  created_at: string
}

export interface HttpLogListResponse {
  items: HttpLog[]
  total: number
  page: number
  page_size: number
}

// ============================================================================
// Memories
// ============================================================================

export interface Memory {
  id: string
  content: string
  memory_type: string
  importance: number
  decay_rate: number
  access_count: number
  source_user: string
  created_at: string
  last_accessed_at: string | null
}

export interface MemoryListResponse {
  items: Memory[]
  total: number
}

// ============================================================================
// Relationship
// ============================================================================

export interface Relationship {
  persona_id: string
  user_id: string
  intimacy: number
  trust: number
  relationship_type: string | null
  notes: string | null
  updated_at: string
}

// ============================================================================
// Health
// ============================================================================

export interface HealthResponse {
  status: string
  version: string
  timestamp: string
}

// ============================================================================
// Prompts (Admin)
// ============================================================================

export interface PromptSummary {
  name: string
  description: string
  placeholders: string[]
  overridden: boolean
  version: number
}

export interface PromptDetail extends PromptSummary {
  current: string
  default: string
}

export interface PromptWriteBody {
  content: string
}

export interface PromptValidateResponse {
  ok: boolean
  missing_placeholders: string[]
  error: string | null
}

export interface PromptHistoryItem {
  filename: string
  mtime: string
  size: number
}

export interface PromptHistoryResponse {
  items: PromptHistoryItem[]
}

// ============================================================================
// Upstream LLM Services (Admin)
// ============================================================================

export type UpstreamModelType = 'main' | 'assist' | 'embedding' | 'rerank'

export interface UpstreamService {
  id: string
  base_url: string
  api_key_masked: string
  created_at: string
  updated_at: string
  models: Partial<Record<UpstreamModelType, string>>
}

export interface UpstreamServiceCreateBody {
  id: string
  base_url: string
  api_key: string
}

export interface UpstreamServiceUpdateBody {
  base_url?: string
  api_key?: string
}

export interface UpstreamModelBindBody {
  model_type: UpstreamModelType
  model: string
}

export interface UpstreamAvailableModels {
  models: string[]
}

// ============================================================================
// Chat (OpenAI Compatible)
// ============================================================================

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

export interface ChatCompletionRequest {
  model?: string
  messages: ChatMessage[]
  temperature?: number
  max_tokens?: number
  stream?: boolean
}

export interface ChatCompletionChoice {
  index: number
  message: ChatMessage
  finish_reason: string
}

export interface ChatCompletionResponse {
  id: string
  object: string
  created: number
  model: string
  choices: ChatCompletionChoice[]
  usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}
