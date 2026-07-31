/** 全局常量 */

// API 基础路径
export const API_BASE = '/panel'
export const CHAT_BASE = '/v1'

// localStorage 键名
export const LOCAL_STORAGE_KEYS = {
  token: 'mnemosync_token',
  darkMode: 'mnemosync_dark',
  debugChatConv: 'mnemosync_debug_chat_conv',
  debugChatSystem: 'mnemosync_debug_chat_system',
  debugChatModel: 'mnemosync_debug_chat_model',
  debugChatMode: 'mnemosync_debug_chat_debug_mode',
  debugApiKey: 'mnemosync_api_key',
} as const

// 虚拟模型 ID: 由 role_bindings 决定实际模型
export const VIRTUAL_MODEL_ANY = 'mnemosync-any'

// 关系阈值
export const RELATIONSHIP_LEVEL_EXCELLENT = 0.85
export const RELATIONSHIP_LEVEL_HIGH = 0.65
export const RELATIONSHIP_LEVEL_MEDIUM = 0.4
export const RELATIONSHIP_LEVEL_LOW = 0.2

/** 构建 URL 查询字符串. 跳过 undefined / null / 空字符串; 保留 false 和 0. */
export function buildQuery(params: object): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params as Record<string, unknown>)) {
    if (value === undefined || value === null || value === '') continue
    qs.set(key, String(value))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}
