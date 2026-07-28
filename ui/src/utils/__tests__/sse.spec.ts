// SSE 解析工具单元测试
import { describe, expect, it } from 'vitest'
import { OPENAI_STREAM_DONE, parseSSEBuffer, parseSSEFrame } from '../sse'

describe('parseSSEFrame', () => {
  it('解析单帧 event + data', () => {
    const frame = 'event: message\ndata: {"hello":"world"}'
    const ev = parseSSEFrame(frame)
    expect(ev).not.toBeNull()
    expect(ev!.event).toBe('message')
    expect(ev!.data).toBe('{"hello":"world"}')
  })

  it('默认 event 为 message', () => {
    const ev = parseSSEFrame('data: hello')
    expect(ev?.event).toBe('message')
    expect(ev?.data).toBe('hello')
  })

  it('拼接多行 data', () => {
    const frame = 'data: line1\ndata: line2'
    expect(parseSSEFrame(frame)?.data).toBe('line1\nline2')
  })

  it('忽略注释行', () => {
    const frame = ': this is a comment\ndata: payload'
    expect(parseSSEFrame(frame)?.data).toBe('payload')
  })

  it('无 data 行返回 null', () => {
    expect(parseSSEFrame('event: ping')).toBeNull()
    expect(parseSSEFrame('')).toBeNull()
  })

  it('去除字段值单个前导空格 (SSE 规范)', () => {
    // 规范: 只去除第一个空格; 多余空格保留
    expect(parseSSEFrame('data: hello')?.data).toBe('hello')
    expect(parseSSEFrame('data:  hello')?.data).toBe(' hello')
    const ev = parseSSEFrame('event: custom\ndata: x')
    expect(ev?.event).toBe('custom')
    expect(ev?.data).toBe('x')
  })
})

describe('parseSSEBuffer', () => {
  it('增量解析完整帧并返回 remainder', () => {
    const buf = 'data: {"a":1}\n\ndata: {"b":2}'
    const { events, remainder } = parseSSEBuffer(buf)
    expect(events).toHaveLength(1)
    expect(events[0]!.data).toBe('{"a":1}')
    expect(remainder).toBe('data: {"b":2}')
  })

  it('解析多帧', () => {
    const buf = 'data: a\n\ndata: b\n\ndata: c\n\n'
    const { events, remainder } = parseSSEBuffer(buf)
    expect(events).toHaveLength(3)
    expect(events.map((e) => e.data)).toEqual(['a', 'b', 'c'])
    expect(remainder).toBe('')
  })

  it('半帧不返回事件', () => {
    const result = parseSSEBuffer('data: partial')
    expect(result.events).toHaveLength(0)
    expect(result.remainder).toBe('data: partial')
  })

  it('空缓冲区', () => {
    const { events, remainder } = parseSSEBuffer('')
    expect(events).toHaveLength(0)
    expect(remainder).toBe('')
  })
})

describe('OPENAI_STREAM_DONE', () => {
  it('值为 [DONE]', () => {
    expect(OPENAI_STREAM_DONE).toBe('[DONE]')
  })
})
