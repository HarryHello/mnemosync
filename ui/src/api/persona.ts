/**Persona API: 人格配置与状态重置. */

import type {
  PersonaConfigRead,
  PersonaConfigUpdateBody,
  PersonaDefinitionRead,
  PersonaDefinitionSaveBody,
  PersonaProfileCreateBody,
  PersonaProfileListResponse,
  PersonaProfileRead,
  PersonaProfileUpdateBody,
  PersonaResetBody,
  PersonaResetResponse,
  PersonaVersionItem,
  PersonaVersionListResponse,
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

// ============================================================================
// Persona Profile API (v0.4.0 多人格)
// ============================================================================

export async function listPersonaProfiles(): Promise<PersonaProfileListResponse> {
  return apiGet<PersonaProfileListResponse>('/admin/persona/profiles')
}

export async function getPersonaProfile(
  personaId: string,
): Promise<PersonaProfileRead> {
  return apiGet<PersonaProfileRead>(`/admin/persona/profiles/${personaId}`)
}

export async function createPersonaProfile(
  body: PersonaProfileCreateBody,
): Promise<PersonaProfileRead> {
  return apiPost<PersonaProfileRead>('/admin/persona/profiles', body)
}

export async function updatePersonaProfile(
  personaId: string,
  body: PersonaProfileUpdateBody,
): Promise<PersonaProfileRead> {
  return apiPut<PersonaProfileRead>(
    `/admin/persona/profiles/${personaId}`,
    body,
  )
}

export async function activatePersonaProfile(
  personaId: string,
): Promise<PersonaProfileRead> {
  return apiPost<PersonaProfileRead>(
    `/admin/persona/profiles/${personaId}/activate`,
  )
}

export async function deletePersonaProfile(
  personaId: string,
): Promise<{ success: boolean; persona_id: string }> {
  return apiDelete<{ success: boolean; persona_id: string }>(
    `/admin/persona/profiles/${personaId}`,
  )
}
