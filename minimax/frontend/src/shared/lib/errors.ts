import { ApiClientError, GatewayNotConfiguredError } from '@/shared/api/client'

export function getErrorMessage(error: unknown): string {
  if (error instanceof GatewayNotConfiguredError || error instanceof ApiClientError) return error.message
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消。'
  if (error instanceof TypeError) return '无法连接到 API 网关。请检查地址、HTTPS、CORS 与网络状态。'
  if (error instanceof Error) return error.message
  return '发生未知错误，请检查网关日志。'
}
