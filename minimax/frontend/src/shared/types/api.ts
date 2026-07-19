export type ApiErrorPayload = {
  code?: string
  message?: string
  requestId?: string
  details?: unknown
}

export type GatewayResponse<T> = {
  data: T
  requestId?: string
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type JobRecord = {
  id: string
  capability: string
  status: JobStatus
  createdAt: string
  updatedAt?: string
  progress?: number
  output?: unknown
  error?: ApiErrorPayload
}

export type FileRecord = {
  id: string
  filename: string
  purpose?: string
  bytes?: number
  createdAt?: string
  url?: string
}

export type ModelOption = {
  id: string
  label: string
  description?: string
}
