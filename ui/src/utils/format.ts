/** 格式化工具函数 */

/**
 * 格式化日期为本地化字符串.
 * @param s ISO 日期字符串或 null/undefined
 * @param locale 区域设置, 默认 zh-CN
 * @returns 格式化后的日期时间, 空值或无效日期返回 em dash
 */
export function formatDate(s: string | null | undefined, locale = 'zh-CN'): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(locale, { hour12: false })
}

/**
 * 格式化日期为仅日期 (无时间).
 */
export function formatDateOnly(s: string | null | undefined, locale = 'zh-CN'): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(locale)
}
