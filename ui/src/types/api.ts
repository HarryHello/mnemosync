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

export interface SetupCredentialsRequest {
  old_password: string
  new_username: string
  new_password: string
}

export interface SetupCredentialsResponse {
  success: boolean
  message: string
}

// ============================================================================
// API Keys
// ============================================================================

export interface ApiKeyCreateRequest {
  note: string
  strategy_id?: string | null
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
  key: string | null
  key_prefix: string
  note: string
  created_at: string
  last_used_at: string | null
  is_active: boolean
  strategy_id: string | null
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
  page: number
  page_size: number
}

// ============================================================================
// Conversation Turns (跨前端上下文流水)
// ============================================================================

export interface ConversationTurn {
  id: number
  role: string  // 'user' | 'assistant'
  content: string
  ts: string  // 平台事件时间
  token_count: number
  source_frontend: string | null
  actor_id: string | null
  effective_user_id: string | null
  display_name: string | null
  external_key: string | null
  space_id: string | null
  external_event_id: string | null
  origin: 'current' | 'history_snapshot' | 'assistant' | 'legacy'
  event_fingerprint: string | null
  observed_at: string
  request_id: string | null
  committed_sequence: number | null
  late_arrival: boolean
  interaction_id: string | null
  event_type: string  // message | tool_call | tool_result
  tool_call_id: string | null
  tool_name: string | null
}

export interface ConversationTurnListResponse {
  items: ConversationTurn[]
  total: number
  page: number
  page_size: number
}

export interface InteractionSummary {
  interaction_id: string
  event_count: number
  first_ts: string
  last_ts: string
  has_tool_calls: boolean
}

export interface InteractionListResponse {
  items: InteractionSummary[]
  total: number
}

// ============================================================================
// Relationship
// ============================================================================

export interface RelationshipIdentityAccount {
  actor_id: string
  frontend: string
  external_key: string
  display_name: string | null
}

export interface RelationshipIdentity {
  kind: 'actor' | 'group'
  name: string | null
  accounts: RelationshipIdentityAccount[]
}

export interface Relationship {
  persona_id: string
  user_id: string
  identity: RelationshipIdentity | null
  intimacy: number
  trust: number
  relationship_type: string | null
  notes: string | null
  updated_at: string
  persona_addressing: string
  user_addressing: string
  context: string
}

export interface RelationshipAuditEntry {
  id: number
  persona_id: string
  user_id: string
  changed_at: string
  source: 'agent' | 'manual'
  field_name: 'persona_addressing' | 'user_addressing' | 'context'
  old_value: string | null
  new_value: string | null
  reason: string
}

export interface RelationshipAuditListResponse {
  items: RelationshipAuditEntry[]
}

export interface RelationshipUpdateBody {
  persona_addressing?: string | null
  user_addressing?: string | null
  context?: string | null
  reason: string
  user_id?: string
}

/** v0.3.0: 多用户关系列表 (分页 + 排序). */
export interface RelationshipListResponse {
  items: Relationship[]
  total: number
  page: number
  page_size: number
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
  /** @deprecated v0.2.3 起模型绑定改由 role_bindings 表管理, UpstreamPage 不再展示 */
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

export interface UpstreamAvailableModels {
  models: string[]
}

// ============================================================================
// Role bindings (v0.2.3 单一真相源)
// ============================================================================

export interface RoleBindingItem {
  role: UpstreamModelType
  priority: number
  service_id: string
  model: string
  created_at: string
  context_length: number | null
  embedding_dim: number | null
  send_dimensions: boolean
}

export interface RoleBindingListResponse {
  items: RoleBindingItem[]
}

export interface RoleBindingAddBody {
  role: UpstreamModelType
  service_id: string
  model: string
  priority?: number | null
  context_length?: number | null
  embedding_dim?: number | null
  send_dimensions?: boolean
}

/**
 * v0.2.13: PATCH /model-bindings/{role}/{priority}.
 * 语义: 键缺失 = 不改; 值为 null (仅 context_length / embedding_dim) = 清空;
 * service_id / model 若下发则必须非空。
 */
export interface RoleBindingUpdateBody {
  service_id?: string
  model?: string
  context_length?: number | null
  embedding_dim?: number | null
  send_dimensions?: boolean
}

export interface RoleBindingReorderBody {
  order: [string, string][]
}

// v0.2.4: 探测 / 重建 / 清理

export interface ProbeDimensionBody {
  service_id: string
  model: string
  dimensions?: number | null
}

export interface ProbeDimensionResponse {
  dimensions: number
}

export interface ReindexStartBody {
  prune?: boolean
  priority_threshold?: number
}

export type ReindexState = 'idle' | 'running' | 'success' | 'error'

export interface ReindexStatusResponse {
  state: ReindexState
  total: number
  processed: number
  pruned: number
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface PruneStartBody {
  priority_threshold?: number
  dry_run?: boolean
}

export interface PruneBreakdown {
  forgotten: number
  expired: number
  low_priority: number
}

export interface PruneResponse {
  total_before: number
  would_delete: number
  deleted: number
  breakdown: PruneBreakdown
}

// ============================================================================
// Persona State Reset (v0.2.7)
// ============================================================================

export interface PersonaResetBody {
  dry_run?: boolean
}

export interface PersonaResetResponse {
  dry_run: boolean
  deleted_memories: number
  deleted_relationships: number
  deleted_conversation_turns: number
  vector_reset: boolean
  errors: string[]
}

// ============================================================================
// Debug Chat (v0.2.5 面板调试)
// ============================================================================

export interface DebugSessionKeyResponse {
  id: string
  key: string
  note: string
  created_at: string
}

export type DebugDirection =
  | 'inbound_request'
  | 'inbound_response'
  | 'upstream_request'
  | 'upstream_response'
  | 'upstream_request_final'
  | 'upstream_response_final'

export interface DebugEventSummary {
  id: string
  correlation_id: string
  ts: number
  direction: DebugDirection | string
  method: string | null
  url: string
  port: number | null
  agent: string | null
  status: number | null
  duration_ms: number | null
  key_note: string | null
  headers: Record<string, string> | null
  body_preview: unknown
  body_full_size: number
  is_truncated: boolean
}

export interface DebugEventListResponse {
  items: DebugEventSummary[]
}

export interface DebugEventDetailResponse {
  summary: DebugEventSummary
  body_full: unknown
  stream_assembled: string | null
  stream_chunks_count: number
}

export interface DebugStatusResponse {
  subscriber_count: number
  buffer_size: number
  buffer_capacity: number
}

// ============================================================================
// Persona Config (v0.2.11 面板人格编辑)
// ============================================================================

export interface PersonaConfigRelation {
  persona_addressing: string
  user_addressing: string
  context: string
}

export interface PersonaConfigRead {
  name: string
  prompt: string
  relation: PersonaConfigRelation
  overridden: boolean
}

export interface PersonaConfigUpdateBody {
  name?: string | null
  prompt?: string | null
  relation?: PersonaConfigRelation | null
}

// ============================================================================
// Structured Persona (v0.3.4, SQLite-based)
// ============================================================================

export interface PersonaIdentityBody {
  personality: string
  speaking_style: string
  values: string[]
  persona_addressing: string
}

export interface PersonaOverrideBody {
  speaking_style: string | null
  personality: string | null
  scenario: string | null
}

export interface PersonaDefinitionRead {
  version: string
  name: string
  identity: PersonaIdentityBody
  space_overrides: Record<string, PersonaOverrideBody>
  created_at: string
  updated_at: string
}

export interface PersonaDefinitionSaveBody {
  name: string
  identity: PersonaIdentityBody
  space_overrides: Record<string, PersonaOverrideBody>
  changelog: string
}

export interface PersonaVersionItem {
  id: number
  version: string
  name: string
  changelog: string | null
  author: string | null
  created_at: string
  active: boolean
}

export interface PersonaVersionListResponse {
  items: PersonaVersionItem[]
  total: number
}

export interface CharacterCardPreview {
  name: string
  source_format: string
  identity: PersonaIdentityBody
  has_lorebook: boolean
  has_examples: boolean
}

// ============================================================================
// Persona Profiles (v0.4.0 多人格)
// ============================================================================

export interface PersonaProfileRead {
  id: string
  name: string
  description: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface PersonaProfileCreateBody {
  name: string
  description: string
}

export interface PersonaProfileUpdateBody {
  name?: string | null
  description?: string | null
}

export interface PersonaProfileListResponse {
  items: PersonaProfileRead[]
  total: number
}

// ============================================================================
// Notifications (v0.2.13 通知中心)
// ============================================================================

export interface Notification {
  id: number
  created_at: string
  level: 'info' | 'warning' | 'error' | string
  category: string
  title: string
  message: string
  meta: Record<string, unknown> | null
  read_at: string | null
}

export interface NotificationListResponse {
  items: Notification[]
  total: number
  page: number
  page_size: number
  unread_count: number
}

export interface UnreadCountResponse {
  unread_count: number
}

export interface MarkReadResponse {
  marked: number
}
// ============================================================================
// ============================================================================ 
// Plugins (v0.3.4)
// ============================================================================

export interface PluginInfo {
  name: string
  description: string
}

export interface PluginListResponse {
  items: PluginInfo[]
  total: number
}

export interface AvailablePluginInfo {
  file_name: string
  download_url: string
  name: string
  description: string
  version: string
  author: string
  installed: boolean
}

export interface AvailablePluginListResponse {
  items: AvailablePluginInfo[]
  total: number
}

export interface InstalledPluginInfo {
  file_name: string
  name: string
  description: string
  version: string
  author: string
}

export interface InstalledPluginListResponse {
  items: InstalledPluginInfo[]
  total: number
}
 


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

// ============================================================================
// Identity (v0.3.0 多用户身份)
// ============================================================================

/** 前台应用上的一个可识别账号, 由 (frontend, external_key) 唯一确定. */
export interface Actor {
  id: string
  external_key: string
  frontend: string
  display_name: string | null
  metadata: string
  created_at: string
  updated_at: string
}

export interface ActorListResponse {
  items: Actor[]
  total: number
}

/** 一个真实人; 多个 Actor 绑定到同一 UserGroup = 跨平台身份. */
export interface UserGroup {
  id: string
  name: string | null
  created_at: string
  updated_at: string
}

export interface UserGroupListResponse {
  items: UserGroup[]
  total: number
}

export interface UserGroupCreateBody {
  name?: string | null
}

export type IdentityStrategyType = 'direct' | 'api_key_bound' | 'regex' | 'llm' | 'plugin'

/** 身份识别策略, 绑定到 API Key, 定义如何从请求中提取身份. */
export interface IdentityStrategy {
  id: string
  name: string
  strategy_type: IdentityStrategyType
  /** JSON 配置字符串 (各策略类型的字段见后端文档). */
  config: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface IdentityStrategyListResponse {
  items: IdentityStrategy[]
  total: number
}

export interface IdentityStrategyCreateBody {
  name: string
  strategy_type: IdentityStrategyType
  config?: string
}

export interface IdentityStrategyUpdateBody {
  name?: string
  config?: string
  is_active?: boolean
}
