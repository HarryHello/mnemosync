import { describe, it, expect } from 'vitest'
import { buildQuery } from '../constants'

describe('buildQuery', () => {
  it('returns empty string for no params', () => {
    expect(buildQuery({})).toBe('')
  })

  it('returns empty string when all values are undefined/null/empty', () => {
    expect(buildQuery({ a: undefined, b: null, c: '' })).toBe('')
  })

  it('skips undefined, null, and empty string values', () => {
    const result = buildQuery({ a: undefined, b: null, c: '', d: 'hello' })
    expect(result).toBe('?d=hello')
  })

  it('preserves boolean false and numeric 0', () => {
    const result = buildQuery({ active: false, count: 0, name: 'test' })
    expect(result).toBe('?active=false&count=0&name=test')
  })

  it('encodes special characters', () => {
    const result = buildQuery({ q: 'hello world', tag: 'a&b' })
    expect(result).toBe('?q=hello+world&tag=a%26b')
  })

  it('converts numbers and booleans to strings', () => {
    const result = buildQuery({ limit: 10, active: true })
    expect(result).toBe('?limit=10&active=true')
  })

  it('handles multiple params in stable order', () => {
    const result = buildQuery({ a: '1', b: '2', c: '3' })
    expect(result).toBe('?a=1&b=2&c=3')
  })
})
