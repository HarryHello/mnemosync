/** 认证状态 Pinia store. */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { UserInfo } from '@/types/api'
import {
  login as apiLogin,
  logout as apiLogout,
  getCurrentUser,
  setToken,
  getToken,
} from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const isAuthenticated = computed(() => !!getToken())

  async function login(username: string, password: string) {
    const result = await apiLogin({ username, password })
    user.value = {
      id: '',
      username: result.username,
      must_change_password: result.must_change_password,
      created_at: '',
      last_login_at: null,
    }
    return result
  }

  async function logout() {
    try {
      await apiLogout()
    } finally {
      user.value = null
      setToken(null)
    }
  }

  async function fetchUser() {
    if (!getToken()) return null
    try {
      const result = await getCurrentUser()
      user.value = result.user
      return result.user
    } catch {
      setToken(null)
      user.value = null
      return null
    }
  }

  return {
    user,
    isAuthenticated,
    login,
    logout,
    fetchUser,
  }
})
