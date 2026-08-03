/**Persona API: 人格配置与状态重置. */

import type {
  CharacterCardPreview,
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
  PersonaVersionListResponse,
} from '@/types/api'
import { apiDelete, apiGet, apiPost, apiPut, API_BASE, getToken } from './http'

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
// Structured Persona API (v0.3.4, SQLite-based)
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

// ============================================================================
// Persona Import / Export
// ============================================================================

/**导入角色卡 (SillyTavern V1/V2 PNG/JSON), 返回解析预览. */
export async function importCharacterCard(file: File): Promise<CharacterCardPreview> {
  const formData = new FormData()
  formData.append('file', file)
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const response = await fetch(`${API_BASE}/admin/persona/import-card`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
  return response.json()
}

/**导出当前激活人格为 JSON 文件 (触发浏览器下载). */
export async function exportPersona(): Promise<void> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const response = await fetch(`${API_BASE}/admin/persona/export`, { headers })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = 'persona.json'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    URL.revokeObjectURL(url)
  }
}
