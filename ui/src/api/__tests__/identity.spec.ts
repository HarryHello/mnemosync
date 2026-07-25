/**
 * 身份管理 API 客户端测试 (v0.3.0).
 *
 * 项目无浏览器驱动验证 (见 no-browser-mocking 约定), 这里只验证
 * client 函数的 URL / method / body 构造正确 — 与后端 14 个身份端点的路径对齐。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  bindActorToGroup,
  createIdentityStrategy,
  createUserGroup,
  deleteIdentityStrategy,
  listActors,
  listActorGroups,
  listGroupMembers,
  listIdentityStrategies,
  listUserGroups,
  unbindActorFromGroup,
  updateIdentityStrategy,
} from '@/api/client'

interface FetchCall {
  url: string
  init: RequestInit | undefined
}

const calls: FetchCall[] = []

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      return jsonResponse({ items: [], total: 0 })
    }),
  )
  localStorage.clear()
})

function last(): FetchCall {
  return calls[calls.length - 1]
}

describe('identity strategy endpoints', () => {
  it('list GETs /panel/admin/identity/strategies', async () => {
    await listIdentityStrategies()
    expect(last().url).toBe('/panel/admin/identity/strategies')
    expect(last().init?.method).toBeUndefined() // 默认 GET
  })

  it('create POSTs body with name/type/config', async () => {
    await createIdentityStrategy({
      name: 'AstrBot QQ',
      strategy_type: 'regex',
      config: '{"frontend":"astrbot"}',
    })
    expect(last().url).toBe('/panel/admin/identity/strategies')
    expect(last().init?.method).toBe('POST')
    expect(JSON.parse(last().init?.body as string)).toEqual({
      name: 'AstrBot QQ',
      strategy_type: 'regex',
      config: '{"frontend":"astrbot"}',
    })
  })

  it('update PATCHes strategy id path', async () => {
    await updateIdentityStrategy('strategy_123', { is_active: false })
    expect(last().url).toBe('/panel/admin/identity/strategies/strategy_123')
    expect(last().init?.method).toBe('PATCH')
  })

  it('delete DELETEs strategy id path', async () => {
    await deleteIdentityStrategy('strategy_abc')
    expect(last().url).toBe('/panel/admin/identity/strategies/strategy_abc')
    expect(last().init?.method).toBe('DELETE')
  })
})

describe('actor / group endpoints', () => {
  it('listActors GETs actors', async () => {
    await listActors()
    expect(last().url).toBe('/panel/admin/identity/actors')
  })

  it('listActorGroups GETs actor groups subpath', async () => {
    await listActorGroups('actor_x')
    expect(last().url).toBe('/panel/admin/identity/actors/actor_x/groups')
  })

  it('listUserGroups GETs groups', async () => {
    await listUserGroups()
    expect(last().url).toBe('/panel/admin/identity/groups')
  })

  it('createUserGroup POSTs optional name', async () => {
    await createUserGroup({ name: '张三' })
    expect(last().url).toBe('/panel/admin/identity/groups')
    expect(last().init?.method).toBe('POST')
    expect(JSON.parse(last().init?.body as string)).toEqual({ name: '张三' })
  })

  it('listGroupMembers GETs group members subpath', async () => {
    await listGroupMembers('group_y')
    expect(last().url).toBe('/panel/admin/identity/groups/group_y/members')
  })

  it('bind POSTs membership path', async () => {
    await bindActorToGroup('actor_x', 'group_y')
    expect(last().url).toBe('/panel/admin/identity/actors/actor_x/groups/group_y')
    expect(last().init?.method).toBe('POST')
  })

  it('unbind DELETEs membership path', async () => {
    await unbindActorFromGroup('actor_x', 'group_y')
    expect(last().url).toBe('/panel/admin/identity/actors/actor_x/groups/group_y')
    expect(last().init?.method).toBe('DELETE')
  })

  it('url-encodes ids with special characters', async () => {
    await listActorGroups('actor/special id')
    expect(last().url).toBe('/panel/admin/identity/actors/actor%2Fspecial%20id/groups')
  })
})
