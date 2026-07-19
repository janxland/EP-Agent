import { fetchForm, fetchJson } from '@/shared/api/client'
import type { FileRecord } from '@/shared/types/api'

export type FileListResponse = { items: FileRecord[]; nextCursor?: string }

export function listFiles(cursor?: string): Promise<FileListResponse> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return fetchJson<FileListResponse>(`/v1/files${query}`)
}

export function uploadFile(file: File, purpose: string): Promise<FileRecord> {
  const form = new FormData()
  form.append('file', file)
  form.append('purpose', purpose)
  return fetchForm<FileRecord>('/v1/files', form, { method: 'POST' })
}

export function removeFile(fileId: string): Promise<void> {
  return fetchJson<void>(`/v1/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' })
}
