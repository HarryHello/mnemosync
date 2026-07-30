// Auth store 单元测试
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '../auth'

// vi.mock 被提升到文件顶部, 用 vi.hoisted 定义被 mock 引用的变量
const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  getCurrentUser: vi.fn(),
  setToken: vi.fn(),
  getToken: vi.fn<() => string | null>(),
}))

vi.mock('@/api/client', () => ({
  login: mocks.login,
  logout: mocks.logout,
  getCurrentUser: mocks.getCurrentUser,
  setToken: mocks.setToken,
  getToken: mocks.getToken,
}))

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('初始状态为未登录', () => {
    mocks.getToken.mockReturnValue(null)
    const store = useAuthStore()
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })

  it('有 token 时 isAuthenticated 为 true', () => {
    mocks.getToken.mockReturnValue('some-token')
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(true)
  })

  it('login 后拉取用户信息', async () => {
    mocks.getToken.mockReturnValue('tok')
    mocks.login.mockResolvedValue({ must_change_password: false })
    mocks.getCurrentUser.mockResolvedValue({
      user: {
        id: 'u1',
        username: 'mnemosync',
        must_change_password: false,
        created_at: '2025-01-01T00:00:00Z',
        last_login_at: null,
      },
    })

    const store = useAuthStore()
    await store.login('mnemosync', 'pass')

    expect(mocks.login).toHaveBeenCalledWith({ username: 'mnemosync', password: 'pass' })
    expect(mocks.getCurrentUser).toHaveBeenCalled()
    expect(store.user).toEqual({
      id: 'u1',
      username: 'mnemosync',
      must_change_password: false,
      created_at: '2025-01-01T00:00:00Z',
      last_login_at: null,
    })
  })

  it('logout 清除 token 和 user', async () => {
    mocks.logout.mockResolvedValue(undefined)
    const store = useAuthStore()
    store.user = {
      id: 'u1', username: 'x', must_change_password: false,
      created_at: '2025-01-01T00:00:00Z', last_login_at: null,
    }

    await store.logout()

    expect(mocks.logout).toHaveBeenCalled()
    expect(mocks.setToken).toHaveBeenCalledWith(null)
    expect(store.user).toBeNull()
  })

  it('fetchUser 失败时清除 token', async () => {
    mocks.getToken.mockReturnValue('tok')
    mocks.getCurrentUser.mockRejectedValue(new Error('401'))

    const store = useAuthStore()
    const result = await store.fetchUser()

    expect(result).toBeNull()
    expect(mocks.setToken).toHaveBeenCalledWith(null)
    expect(store.user).toBeNull()
  })

  it('fetchUser 无 token 时直接返回 null', async () => {
    mocks.getToken.mockReturnValue(null)
    const store = useAuthStore()
    const result = await store.fetchUser()
    expect(result).toBeNull()
    expect(mocks.getCurrentUser).not.toHaveBeenCalled()
  })
})
