import { fetchJson } from '@/shared/api/client'
import type { JobRecord, JobStatus } from '@/shared/types/api'

export type JobListResponse = { items: JobRecord[]; nextCursor?: string }

export function listJobs(params?: { status?: JobStatus; cursor?: string }): Promise<JobListResponse> {
  const search = new URLSearchParams()
  if (params?.status) search.set('status', params.status)
  if (params?.cursor) search.set('cursor', params.cursor)
  const queryString = search.toString()
  const query = queryString ? `?${queryString}` : ''
  return fetchJson<JobListResponse>(`/v1/jobs${query}`)
}

export function getJob(jobId: string): Promise<JobRecord> {
  return fetchJson<JobRecord>(`/v1/jobs/${encodeURIComponent(jobId)}`)
}

export function cancelJob(jobId: string): Promise<JobRecord> {
  return fetchJson<JobRecord>(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}
