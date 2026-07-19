import { fetchEventStream } from '@/shared/api/client'

export type TextMessage = { role: 'system' | 'user' | 'assistant'; content: string }

export type TextChatRequest = {
  model: string
  messages: TextMessage[]
  temperature?: number
  topP?: number
}

export type TextStreamDelta = {
  delta?: string
  content?: string
  text?: string
  error?: { message?: string }
}

export async function streamTextChat(
  request: TextChatRequest,
  options: { signal?: AbortSignal; onDelta: (text: string) => void; onMeta?: (value: unknown) => void },
): Promise<void> {
  await fetchEventStream('/v1/text/chat', request, {
    signal: options.signal,
    onEvent: (event) => {
      if (event.done || !event.data) return
      try {
        const payload = JSON.parse(event.data) as TextStreamDelta
        if (payload.error?.message) throw new Error(payload.error.message)
        const delta = payload.delta ?? payload.content ?? payload.text
        if (delta) options.onDelta(delta)
        else options.onMeta?.(payload)
      } catch (error) {
        if (error instanceof SyntaxError) options.onDelta(event.data)
        else throw error
      }
    },
  })
}
