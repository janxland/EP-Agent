import { fetchJson } from '@/shared/api/client'
import type { JobRecord } from '@/shared/types/api'

export type VoiceCloneRequest = {
  fileId: string
  voiceId: string
  name?: string
  language?: string
  promptAudioFileId?: string
  promptText?: string
}

export type VoiceCloneResponse = JobRecord | { voiceId: string; status?: string }

export function createVoiceClone(request: VoiceCloneRequest): Promise<VoiceCloneResponse> {
  return fetchJson<VoiceCloneResponse>('/v1/voice-clones', { method: 'POST', body: request })
}

export function deleteVoiceClone(voiceId: string): Promise<void> {
  return fetchJson<void>(`/v1/voice-clones/${encodeURIComponent(voiceId)}`, { method: 'DELETE' })
}
