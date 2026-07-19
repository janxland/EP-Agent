import { createSseParser, type SseEvent } from '@/shared/sse/parser'
import type { ApiErrorPayload } from '@/shared/types/api'

const GATEWAY_STORAGE_KEY = 'minimax-console.gateway-url'

export class GatewayNotConfiguredError extends Error {
  constructor() {
    super('尚未配置安全 API 网关地址。请在右上角“网关设置”中填写你自己的可信网关。')
    this.name = 'GatewayNotConfiguredError'
  }
}

export class ApiClientError extends Error {
  readonly status: number
  readonly code?: string
  readonly requestId?: string
  readonly details?: unknown

  constructor(message: string, status: number, payload?: ApiErrorPayload) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = payload?.code
    this.requestId = payload?.requestId
    this.details = payload?.details
  }
}

function normalizeGatewayUrl(value?: string | null): string {
  return (value ?? '').trim().replace(/\/+$/, '')
}

export function getGatewayUrl(): string {
  if (typeof window !== 'undefined') {
    const saved = normalizeGatewayUrl(window.localStorage.getItem(GATEWAY_STORAGE_KEY))
    if (saved) return saved
  }
  return normalizeGatewayUrl(process.env.NEXT_PUBLIC_MINIMAX_GATEWAY_URL)
}

export function saveGatewayUrl(url: string): void {
  if (typeof window === 'undefined') return
  const normalized = normalizeGatewayUrl(url)
  if (normalized) window.localStorage.setItem(GATEWAY_STORAGE_KEY, normalized)
  else window.localStorage.removeItem(GATEWAY_STORAGE_KEY)
}

export function assertGatewayUrl(): string {
  const gateway = getGatewayUrl()
  if (!gateway) throw new GatewayNotConfiguredError()
  return gateway
}

function createUrl(path: string): string {
  const gateway = assertGatewayUrl()
  return `${gateway}${path.startsWith('/') ? path : `/${path}`}`
}

async function parseError(response: Response): Promise<ApiClientError> {
  let payload: ApiErrorPayload | undefined
  try {
    payload = (await response.json()) as ApiErrorPayload
  } catch {
    payload = undefined
  }
  const requestId = response.headers.get('x-request-id') ?? payload?.requestId
  return new ApiClientError(
    payload?.message || `网关请求失败（HTTP ${response.status}）`,
    response.status,
    { ...payload, requestId: requestId ?? undefined },
  )
}

type JsonRequestOptions = Omit<RequestInit, 'body'> & { body?: unknown }

export async function fetchJson<T>(path: string, options: JsonRequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')

  const response = await fetch(createUrl(path), {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export async function fetchForm<T>(path: string, formData: FormData, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  const response = await fetch(createUrl(path), { ...options, headers, body: formData })
  if (!response.ok) throw await parseError(response)
  return (await response.json()) as T
}

export type StreamCallbacks = {
  onEvent: (event: SseEvent) => void
  onComment?: (comment: string) => void
  signal?: AbortSignal
}

export async function fetchEventStream(
  path: string,
  body: unknown,
  callbacks: StreamCallbacks,
): Promise<void> {
  const response = await fetch(createUrl(path), {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: callbacks.signal,
  })
  if (!response.ok) throw await parseError(response)
  if (!response.body) throw new ApiClientError('网关未返回可读取的流。', response.status)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let sawDone = false
  const parser = createSseParser({
    onComment: callbacks.onComment,
    onEvent: (event) => {
      callbacks.onEvent(event)
      if (event.done) sawDone = true
    },
  })

  try {
    while (!sawDone) {
      const { value, done } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    parser.feed(decoder.decode())
    parser.flush()
  } finally {
    reader.releaseLock()
  }
}
