/** 通知中心 Pinia store (v0.2.13).
 *
 * 后端: /panel/admin/notifications (list / unread-count / mark-read / mark-all-read / delete)
 * 前端: 侧栏头像 badge 显示未读数; "显示通知" 抽屉展示详情。
 *
 * 轮询策略: 60s 定时 + tab 可见变化时立即拉一次。抽屉打开时拉完整列表, 关闭后停轮询列表 (仅保 unread-count 心跳)。
 */

import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref } from 'vue'
import type { Notification, NotificationListResponse } from '@/types/api'
import {
  deleteNotification as apiDelete,
  deleteReadNotifications as apiDeleteRead,
  getToken,
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/api/client'

const POLL_INTERVAL_MS = 60_000
const DEFAULT_PAGE_SIZE = 50

export const useNotificationsStore = defineStore('notifications', () => {
  const items = ref<Notification[]>([])
  const total = ref(0)
  const unreadCount = ref(0)
  const loading = ref(false)
  const page = ref(1)
  const pageSize = ref(DEFAULT_PAGE_SIZE)

  const hasUnread = computed(() => unreadCount.value > 0)
  const readCount = computed(() => items.value.filter((n) => !!n.read_at).length)

  let timer: ReturnType<typeof setInterval> | null = null
  let visibilityListenerAttached = false

  async function fetchUnreadCount() {
    if (!getToken()) return
    try {
      const r = await getUnreadNotificationCount()
      unreadCount.value = r.unread_count
    } catch (e) {
      // 401: token 已失效, 停止轮询避免持续打 401
      if ((e as Error).message?.includes('401')) {
        stopPolling()
      }
      // 其他错误静默失败, 不打扰 UI
    }
  }

  async function fetchList(opts: { unreadOnly?: boolean; page?: number } = {}) {
    if (!getToken()) return
    loading.value = true
    if (opts.page) page.value = opts.page
    try {
      const r: NotificationListResponse = await listNotifications({
        page: page.value,
        page_size: pageSize.value,
        unread_only: opts.unreadOnly ?? false,
      })
      items.value = r.items
      total.value = r.total
      unreadCount.value = r.unread_count
    } finally {
      loading.value = false
    }
  }

  async function markRead(id: number) {
    try {
      await markNotificationRead(id)
      const it = items.value.find((n) => n.id === id)
      if (it && !it.read_at) {
        it.read_at = new Date().toISOString()
        unreadCount.value = Math.max(0, unreadCount.value - 1)
      }
    } catch (e) {
      console.error('[notifications] markRead failed', e)
      throw e
    }
  }

  async function markAllRead() {
    try {
      const r = await markAllNotificationsRead()
      if (r.marked > 0) {
        const stamp = new Date().toISOString()
        for (const n of items.value) if (!n.read_at) n.read_at = stamp
        unreadCount.value = 0
      }
    } catch (e) {
      console.error('[notifications] markAllRead failed', e)
      throw e
    }
  }

  async function remove(id: number) {
    try {
      await apiDelete(id)
      const idx = items.value.findIndex((n) => n.id === id)
      if (idx >= 0) {
        const target = items.value[idx]
        const wasUnread = target ? !target.read_at : false
        items.value.splice(idx, 1)
        total.value = Math.max(0, total.value - 1)
        if (wasUnread) unreadCount.value = Math.max(0, unreadCount.value - 1)
      }
    } catch (e) {
      console.error('[notifications] remove failed', e)
      throw e
    }
  }

  async function removeRead() {
    try {
      const r = await apiDeleteRead()
      if (r.deleted > 0) {
        items.value = items.value.filter((n) => !n.read_at)
        total.value = Math.max(0, total.value - r.deleted)
      }
      return r.deleted
    } catch (e) {
      console.error('[notifications] removeRead failed', e)
      throw e
    }
  }

  function _onVisibility() {
    if (document.visibilityState === 'visible') fetchUnreadCount()
  }

  function startPolling() {
    if (timer !== null) return
    fetchUnreadCount()
    timer = setInterval(fetchUnreadCount, POLL_INTERVAL_MS)
    if (!visibilityListenerAttached) {
      document.addEventListener('visibilitychange', _onVisibility)
      visibilityListenerAttached = true
    }
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    if (visibilityListenerAttached) {
      document.removeEventListener('visibilitychange', _onVisibility)
      visibilityListenerAttached = false
    }
  }

  onScopeDispose(() => {
    stopPolling()
  })

  return {
    items,
    total,
    unreadCount,
    loading,
    page,
    pageSize,
    hasUnread,
    readCount,
    fetchUnreadCount,
    fetchList,
    markRead,
    markAllRead,
    remove,
    removeRead,
    startPolling,
    stopPolling,
  }
})
