import { describe, expect, it } from 'vitest'
import { formatTokenLimit, parseTokenLimit } from '../tokenFormat'

describe('parseTokenLimit (手输解析, 十进制)', () => {
  it('K/M 按十进制转换', () => {
    expect(parseTokenLimit('128K')).toBe(128_000)
    expect(parseTokenLimit('1M')).toBe(1_000_000)
    expect(parseTokenLimit('1.5M')).toBe(1_500_000)
    expect(parseTokenLimit('0.5K')).toBe(500)
    expect(parseTokenLimit('200k')).toBe(200_000)
  })

  it('纯数字原样 (取整, 至少 1)', () => {
    expect(parseTokenLimit('1000000')).toBe(1_000_000)
    expect(parseTokenLimit('131072')).toBe(131_072)
    expect(parseTokenLimit('0.4')).toBe(1)
  })

  it('容忍空格与空值', () => {
    expect(parseTokenLimit(' 128 K ')).toBe(128_000)
    expect(parseTokenLimit('')).toBeNull()
    expect(parseTokenLimit(null)).toBeNull()
    expect(parseTokenLimit(undefined)).toBeNull()
  })

  it('非法格式拒绝', () => {
    expect(parseTokenLimit('abc')).toBeNull()
    expect(parseTokenLimit('1.2.3K')).toBeNull()
    expect(parseTokenLimit('12Ki')).toBeNull()
  })
})

describe('formatTokenLimit (显示, 双制式探测)', () => {
  it('十进制整除优先 (models.dev 高频口径)', () => {
    expect(formatTokenLimit(1_000_000)).toBe('1M')
    expect(formatTokenLimit(2_000_000)).toBe('2M')
    expect(formatTokenLimit(128_000)).toBe('128K')
    expect(formatTokenLimit(200_000)).toBe('200K')
    expect(formatTokenLimit(384_000)).toBe('384K')
    expect(formatTokenLimit(1050_000)).toBe('1050K')
  })

  it('二进制整除回退 (传统厂商口径)', () => {
    expect(formatTokenLimit(131_072)).toBe('128K')
    expect(formatTokenLimit(262_144)).toBe('256K')
    expect(formatTokenLimit(1_048_576)).toBe('1M')
    expect(formatTokenLimit(32_768)).toBe('32K')
    expect(formatTokenLimit(8_192)).toBe('8K')
  })

  it('不整除显示原数字', () => {
    expect(formatTokenLimit(1_047_576)).toBe('1047576')
    expect(formatTokenLimit(999)).toBe('999')
  })

  it('空值返回空串', () => {
    expect(formatTokenLimit(0)).toBe('')
    expect(formatTokenLimit(null)).toBe('')
    expect(formatTokenLimit(undefined)).toBe('')
  })
})
