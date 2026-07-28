/**Persona API: 人格配置与状态重置. */

import type {
  PersonaConfigRead,
  PersonaConfigUpdateBody,
  PersonaResetBody,
  PersonaResetResponse,
} from '@/types/api'
import { apiDelete, apiGet, apiPost, apiPut } from './http'

export async function resetPersona(
  body: PersonaResetBody = {},
): Promise<PersonaResetResponse> {
  return apiPost<PersonaResetResponse>('/admin/persona/reset', body)
}

export async function getPersonaConfig(): Promise<PersonaConfigRead> {
  return apiGet<PersonaConfigRead>('/admin/persona')
}

export async function updatePersonaConfig(
  body: PersonaConfigUpdateBody,
): Promise<PersonaConfigRead> {
  return apiPut<PersonaConfigRead>('/admin/persona', body)
}

export async function resetPersonaConfig(): Promise<PersonaConfigRead> {
  return apiDelete<PersonaConfigRead>('/admin/persona')
}

// ============================================================================
// Structured Persona API (v0.3.3, SQLite-based)
// ============================================================================

export interface PersonaIdentityBody {
  personality: string
  speaking_style: string
  values: string[]
  persona_addressing: string
  user_addressing: string
  context: string
}

export interface PersonaOverrideBody {
  speaking_style: string | null
  personality: string | null
  context: string | null
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

export async function getPersonaDefinition(): Promise<PersonaDefinitionRead> {
  return apiGet<PersonaDefinitionRead>('/admin/persona/definition')
}

export async function savePersonaDefinition(
  body: PersonaDefinitionSaveBody,
): Promise<PersonaDefinitionRead> {
  return apiPut<PersonaDefinitionRead>('/admin/persona/definition', body)
}

export async function listPersonaVersions(
  limit: number = 50,
): Promise<PersonaVersionListResponse> {
  return apiGet<PersonaVersionListResponse>(
    `/admin/persona/versions?limit=${limit}`,
  )
}

export async function rollbackPersonaVersion(
  versionId: number,
): Promise<{ success: boolean; version_id: number }> {
  return apiPost<{ success: boolean; version_id: number }>(
    `/admin/persona/versions/${versionId}/rollback`,
  )
}
