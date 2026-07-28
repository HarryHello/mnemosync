/**Auth API: 登录/登出/用户信息/凭证设置. */

import type {
  ChangePasswordRequest,
  LoginRequest,
  LoginResponse,
  SetupCredentialsRequest,
  SetupCredentialsResponse,
  UserInfoResponse,
} from '@/types/api'
import { apiGet, apiPost, setToken } from './http'

export async function login(data: LoginRequest): Promise<LoginResponse> {
  const result = await apiPost<LoginResponse>('/auth/login', data)
  setToken(result.access_token)
  return result
}

export async function logout(): Promise<void> {
  await apiPost('/auth/logout')
  setToken(null)
}

export async function getCurrentUser(): Promise<UserInfoResponse> {
  return apiGet<UserInfoResponse>('/auth/me')
}

export async function changePassword(
  data: ChangePasswordRequest,
): Promise<{ success: boolean }> {
  return apiPost('/auth/change-password', data)
}

export async function setupCredentials(
  data: SetupCredentialsRequest,
): Promise<SetupCredentialsResponse> {
  return apiPost<SetupCredentialsResponse>('/auth/setup-credentials', data)
}

export { setToken, getToken } from './http'
