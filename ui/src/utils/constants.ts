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
