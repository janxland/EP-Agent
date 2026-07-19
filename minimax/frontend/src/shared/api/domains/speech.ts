import { fetchJson } from '@/shared/api/client'
import type { JobRecord } from '@/shared/types/api'

export type SpeechRequest = {
  model: string
  text: string
  voiceId?: string
  speed?: number
  volume?: number
  pitch?: number
  format?: 'mp3' | 'wav' | 'pcm' | 'flac'
  languageBoost?: string
}

export type SpeechResponse = JobRecord | { audioUrl: string; jobId?: string; durationMs?: number }

export function synthesizeSpeech(request: SpeechRequest): Promise<SpeechResponse> {
  return fetchJson<SpeechResponse>('/v1/speech/synthesize', { method: 'POST', body: request })
}
