/**Identity API: 身份识别策略、Actor、UserGroup 管理. */

import type {
  AvailablePluginListResponse,
  InstalledPluginListResponse,
  PluginListResponse,

  Actor,
  ActorListResponse,
  IdentityStrategy,
  IdentityStrategyCreateBody,
  IdentityStrategyListResponse,
  IdentityStrategyUpdateBody,
  UserGroup,
  UserGroupCreateBody,
  UserGroupListResponse,
} from '@/types/api'
import { apiDelete, apiGet, apiPatch, apiPost } from './http'

// ============================================================================
// 身份识别策略
// ============================================================================

export async function listIdentityStrategies(): Promise<IdentityStrategyListResponse> {
  return apiGet<IdentityStrategyListResponse>('/admin/identity/strategies')
}

export async function createIdentityStrategy(
  body: IdentityStrategyCreateBody,
): Promise<IdentityStrategy> {
  return apiPost<IdentityStrategy>('/admin/identity/strategies', body)
}

export async function getIdentityStrategy(
  strategyId: string,
): Promise<IdentityStrategy> {
  return apiGet<IdentityStrategy>(
    `/admin/identity/strategies/${encodeURIComponent(strategyId)}`,
  )
}

export async function updateIdentityStrategy(
  strategyId: string,
  body: IdentityStrategyUpdateBody,
): Promise<IdentityStrategy> {
  return apiPatch<IdentityStrategy>(
    `/admin/identity/strategies/${encodeURIComponent(strategyId)}`,
    body,
  )
}

export async function deleteIdentityStrategy(strategyId: string): Promise<void> {
  await apiDelete(
    `/admin/identity/strategies/${encodeURIComponent(strategyId)}`,
  )
}

export interface GenerateConfigBody {
  strategy_type: string
  description: string
  sample_message?: string | null
}

export interface GenerateConfigResponse {
  config: string
}

export async function generateStrategyConfig(
  body: GenerateConfigBody,
): Promise<GenerateConfigResponse> {
  return apiPost<GenerateConfigResponse>(
    '/admin/identity/strategies/generate-config',
    body,
  )
}

// ============================================================================
// Actors
// ============================================================================

export async function listActors(): Promise<ActorListResponse> {
  return apiGet<ActorListResponse>('/admin/identity/actors')
}

export async function getActor(actorId: string): Promise<Actor> {
  return apiGet<Actor>(
    `/admin/identity/actors/${encodeURIComponent(actorId)}`,
  )
}

export async function listActorGroups(
  actorId: string,
): Promise<UserGroupListResponse> {
  return apiGet<UserGroupListResponse>(
    `/admin/identity/actors/${encodeURIComponent(actorId)}/groups`,
  )
}

// ============================================================================
// UserGroups
// ============================================================================

export async function listUserGroups(): Promise<UserGroupListResponse> {
  return apiGet<UserGroupListResponse>('/admin/identity/groups')
}

export async function createUserGroup(
  body: UserGroupCreateBody = {},
): Promise<UserGroup> {
  return apiPost<UserGroup>('/admin/identity/groups', body)
}

export async function getUserGroup(groupId: string): Promise<UserGroup> {
  return apiGet<UserGroup>(
    `/admin/identity/groups/${encodeURIComponent(groupId)}`,
  )
}

export async function listGroupMembers(
  groupId: string,
): Promise<ActorListResponse> {
  return apiGet<ActorListResponse>(
    `/admin/identity/groups/${encodeURIComponent(groupId)}/members`,
  )
}

// ============================================================================
// Actor ↔ Group 绑定
// ============================================================================

export async function bindActorToGroup(
  actorId: string,
  groupId: string,
): Promise<void> {
  await apiPost(
    `/admin/identity/actors/${encodeURIComponent(actorId)}/groups/${encodeURIComponent(groupId)}`,
    { actor_id: actorId, group_id: groupId },
  )
}

export async function unbindActorFromGroup(
  actorId: string,
  groupId: string,
): Promise<void> {
  await apiDelete(
    `/admin/identity/actors/${encodeURIComponent(actorId)}/groups/${encodeURIComponent(groupId)}`,
  )
}

// ============================================================================
// Identity Plugins
// ============================================================================

export async function listPlugins(): Promise<PluginListResponse> {
  return apiGet<PluginListResponse>('/admin/identity/plugins')
}

export async function listAvailablePlugins(): Promise<AvailablePluginListResponse> {
  return apiGet<AvailablePluginListResponse>('/admin/identity/plugins/available')
}

export async function listInstalledPlugins(): Promise<InstalledPluginListResponse> {
  return apiGet<InstalledPluginListResponse>('/admin/identity/plugins/installed')
}

export async function installPlugin(
  fileName: string,
  downloadUrl: string,
): Promise<{ success: boolean; file_name: string; path: string }> {
  return apiPost('/admin/identity/plugins/install', {
    file_name: fileName,
    download_url: downloadUrl,
  })
}

export async function removePlugin(fileName: string): Promise<void> {
  await apiDelete(`/admin/identity/plugins/${encodeURIComponent(fileName)}`)
}
