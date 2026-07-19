import { fetchJson } from '@/shared/api/client'
import type { JobRecord } from '@/shared/types/api'

export type ImageGenerationRequest = {
  model: string
  prompt: string
  aspectRatio?: string
  count?: number
  subjectReferenceFileId?: string
}

export type VideoGenerationRequest = {
  model: string
  prompt: string
  firstFrameFileId?: string
  duration?: number
  resolution?: string
}

export type MusicGenerationRequest = {
  model: string
  prompt: string
  lyrics?: string
  instrumental?: boolean
  referenceFileId?: string
}

export type GenerationResponse = JobRecord | { jobId: string }

export const generateImage = (request: ImageGenerationRequest) =>
  fetchJson<GenerationResponse>('/v1/images/generations', { method: 'POST', body: request })

export const generateVideo = (request: VideoGenerationRequest) =>
  fetchJson<GenerationResponse>('/v1/videos/generations', { method: 'POST', body: request })

export const generateMusic = (request: MusicGenerationRequest) =>
  fetchJson<GenerationResponse>('/v1/music/generations', { method: 'POST', body: request })
