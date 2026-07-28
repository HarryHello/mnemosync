/** 错误处理工具函数 */

/**
 * 从 unknown 类型的错误中提取人类可读的错误消息.
 * @param err catch 块中捕获的任意值
 * @returns 错误消息字符串
 */
export function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}
