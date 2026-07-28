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
