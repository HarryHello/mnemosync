/**K/M 单位解析与显示 (模型上下文/输出上限).

约定 (v0.4.1-beta.10 起按用户决策统一):
- 解析 (手输): 十进制 — K=1_000, M=1_000_000. 十进制算出的值较小,
  对模型窗口来说绝对不会超限 (二进制 1M=1048576 会高估 4.9%).
- 显示: 双制式探测 — 先十进制整除 (1000000→1M, 128000→128K),
  再二进制整除 (131072→128K, 262144→256K), 都不整除显示原数字.
- 拉取链路 (上游 /models, models.dev) 不经过解析, 厂商原始整数直存.
*/

/** 解析带 K/M 后缀的文本为 token 数; 无效返回 null. */
export function parseTokenLimit(v: string | undefined | null): number | null {
  const sv = (v || '').trim()
  if (!sv) return null
  const s = sv.toUpperCase()
  const m = /^(\d+(?:\.\d+)?)\s*([KM]?)$/.exec(s)
  if (!m) return null
  const n = parseFloat(m[1] ?? '')
  if (Number.isNaN(n)) return null
  if (m[2] === 'K') return Math.max(1, Math.round(n * 1_000))
  if (m[2] === 'M') return Math.max(1, Math.round(n * 1_000_000))
  return Math.max(1, Math.round(n))
}

/** token 数格式化为 K/M 文本 (纯显示, 不改变存储值). */
export function formatTokenLimit(n: number | null | undefined): string {
  if (!n) return ''
  // 十进制整除优先: models.dev 高频值 (1000000/128000/200000) 的原生口径
  if (n >= 1_000_000 && n % 1_000_000 === 0) return String(n / 1_000_000) + 'M'
  if (n >= 1_000 && n % 1_000 === 0) return String(n / 1_000) + 'K'
  // 二进制整除: 传统厂商口径 (131072=128K, 262144=256K, 1048576=1M)
  if (n >= 1024 * 1024 && n % (1024 * 1024) === 0) return String(n / (1024 * 1024)) + 'M'
  if (n >= 1024 && n % 1024 === 0) return String(n / 1024) + 'K'
  return String(n)
}
