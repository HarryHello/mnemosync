/**SSE (Server-Sent Events) 解析工具.
 *
 * 统一解析 ``text/event-stream`` 帧, 消除 ``stores/debug.ts`` 与 ``views/DebugChatPage.vue``
 * 中的重复 ``TextDecoder + buf.indexOf('\\n\\n')`` 样板代码。
 *
 * 帧格式 (W3C SSE 规范子集):
 *   - 帧之间由 ``\\n\\n`` 分隔
 *   - 每帧内每行以 ``field:value`` 形式出现
 *   - 以 ``:`` 开头的行为注释, 忽略
 *   - ``event: <name>`` 指定事件类型 (默认 ``message``)
 *   - ``data: <payload>`` 可出现多次, 拼接为多行数据
 */

export interface SSEEvent {
  /**事件类型, 默认 ``message``. */
  event: string
  /**合并后的数据载荷 (多行 ``data:`` 以 ``\\n`` 连接). */
  data: string
}

/**将缓冲区中的 SSE 帧增量解析为事件列表.
 *
 * @param buf 累积的文本缓冲区 (含未消费完的半帧)
 * @returns events: 已完整解析的事件列表; remainder: 剩余未消费半帧
 *
 * @example
 * ```ts
 * let buf = ''
 * const reader = resp.body!.getReader()
 * const decoder = new TextDecoder('utf-8')
 * while (true) {
 *   const { value, done } = await reader.read()
 *   if (done) break
 *   buf += decoder.decode(value, { stream: true })
 *   const { events, remainder } = parseSSEBuffer(buf)
 *   buf = remainder
 *   for (const ev of events) handle(ev)
 * }
 * ```
 */
export function parseSSEBuffer(buf: string): { events: SSEEvent[]; remainder: string } {
  const events: SSEEvent[] = []
  let rest = buf
  while (true) {
    const idx = rest.indexOf('\n\n')
    if (idx === -1) break
    const frame = rest.slice(0, idx)
    rest = rest.slice(idx + 2)
    const ev = parseSSEFrame(frame)
    if (ev) events.push(ev)
  }
  return { events, remainder: rest }
}

/**解析单个 SSE 帧 (不含分隔符 ``\\n\\n``). 无 ``data:`` 行时返回 null. */
export function parseSSEFrame(frame: string): SSEEvent | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (!line || line.startsWith(':')) continue
    // 允许字段值前有一个空格 (``event: xxx`` 中的空格)
    if (line.startsWith('event:')) {
      event = line.slice(6).replace(/^ /, '')
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  if (!dataLines.length) return null
  return { event, data: dataLines.join('\n') }
}

/**OpenAI 流式 ``data: [DONE]`` 哨兵常量. */
export const OPENAI_STREAM_DONE = '[DONE]'
