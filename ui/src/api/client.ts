/**API 服务函数 — barrel 导出.
 *
 * 各域模块拆分到独立文件, 本文件仅做 re-export 保持向后兼容
 * (所有现有 `from '@/api/client'` 导入无需修改).
 */

// HTTP 基础层 (token 管理 + request 封装)
export {
  getToken,
  setToken,
  request,
  apiGet,
  apiPost,
  apiPut,
  apiPatch,
  apiDelete,
} from './http'
export { API_BASE } from './http'

// 域模块
export * from './auth'
export * from './api_keys'
export * from './core'
export * from './memories'
export * from './conversation'
export * from './prompts'
export * from './upstream'
export * from './persona'
export * from './debug'
export * from './notifications'
export * from './identity'
export * from './chat'
