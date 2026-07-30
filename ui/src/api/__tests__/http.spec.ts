import { describe, it, expect, beforeEach, vi } from 'vitest'
import { getToken, setToken, request } from '../http'
import { LOCAL_STORAGE_KEYS } from '../../utils/constants'

// oxlint-disable-next-line vitest/require-mock-type-parameters
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

describe('http', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setToken(null)
  })

  describe('token management', () => {
    it('setToken stores token in memory and localStorage', () => {
      setToken('test-token')
      expect(getToken()).toBe('test-token')
      expect(localStorage.getItem(LOCAL_STORAGE_KEYS.token)).toBe('test-token')
    })

    it('setToken(null) removes token', () => {
      setToken('test-token')
      setToken(null)
      expect(getToken()).toBeNull()
      expect(localStorage.getItem(LOCAL_STORAGE_KEYS.token)).toBeNull()
    })
  })

  describe('request', () => {
    it('includes Authorization header when token is set', async () => {
      setToken('my-token')
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: () => Promise.resolve({ data: 'ok' }),
      })

      const result = await request<{ data: string }>('/test')
      expect(result).toEqual({ data: 'ok' })
      expect(mockFetch).toHaveBeenCalledWith('/test', {
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer my-token',
        },
      })
    })

    it('omits Authorization header when no token', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: () => Promise.resolve({ data: 'ok' }),
      })

      await request('/test')
      const init = mockFetch.mock.calls[0]?.[1]
      const calledHeaders = init?.headers as Record<string, string> | undefined
      expect(calledHeaders?.Authorization).toBeUndefined()
      expect(calledHeaders?.['Content-Type']).toBe('application/json')
    })

    it('throws on non-200 response', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        headers: { get: () => 'application/json' },
        json: () => Promise.resolve({ detail: 'Bad request' }),
      })

      await expect(request('/test')).rejects.toThrow('Bad request')
    })

    it('handles 204 no content', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 204,
        headers: { get: () => '' },
      })

      const result = await request<void>('/test')
      expect(result).toBeUndefined()
    })
  })
})
