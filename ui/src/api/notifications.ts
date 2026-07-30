/**Notifications API: 面板通知中心. */

import type {
  MarkReadResponse,
  Notification,
  NotificationListResponse,
  UnreadCountResponse,
} from '@/types/api'
import { buildQuery } from '@/utils/constants'
import { apiDelete, apiGet, apiPost } from './http'

export interface NotificationListParams {
  page?: number
  page_size?: number
  unread_only?: boolean
}

export async function listNotifications(
  params: NotificationListParams = {},
): Promise<NotificationListResponse> {
  return apiGet<NotificationListResponse>(
    `/admin/notifications${buildQuery(params)}`,
  )
}

export async function getUnreadNotificationCount(): Promise<UnreadCountResponse> {
  return apiGet<UnreadCountResponse>('/admin/notifications/unread-count')
}

export async function markNotificationRead(
  id: number,
): Promise<MarkReadResponse> {
  return apiPost<MarkReadResponse>(`/admin/notifications/${id}/read`)
}

export async function markAllNotificationsRead(): Promise<MarkReadResponse> {
  return apiPost<MarkReadResponse>('/admin/notifications/mark-all-read')
}

export async function deleteNotification(id: number): Promise<void> {
  await apiDelete(`/admin/notifications/${id}`)
}

export async function deleteReadNotifications(): Promise<{ deleted: number }> {
  return apiDelete<{ deleted: number }>('/admin/notifications/read')
}

export type { Notification }
