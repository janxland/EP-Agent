export type SseEvent = {
  event?: string
  id?: string
  retry?: number
  data: string
  done: boolean
}

type ParserOptions = {
  onEvent: (event: SseEvent) => void
  onComment?: (comment: string) => void
}

export type SseParser = {
  feed: (chunk: string) => void
  flush: () => void
  reset: () => void
}

export function createSseParser(options: ParserOptions): SseParser {
  let buffer = ''
  let dataLines: string[] = []
  let eventName: string | undefined
  let eventId: string | undefined
  let retry: number | undefined

  const dispatch = () => {
    if (dataLines.length === 0 && !eventName && !eventId && retry === undefined) return

    const data = dataLines.join('\n')
    options.onEvent({
      event: eventName,
      id: eventId,
      retry,
      data,
      done: data.trim() === '[DONE]',
    })

    dataLines = []
    eventName = undefined
    eventId = undefined
    retry = undefined
  }

  const processLine = (rawLine: string) => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (line === '') {
      dispatch()
      return
    }
    if (line.startsWith(':')) {
      options.onComment?.(line.slice(1).trimStart())
      return
    }

    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'data') dataLines.push(value)
    if (field === 'event') eventName = value
    if (field === 'id' && !value.includes('\u0000')) eventId = value
    if (field === 'retry' && /^\d+$/.test(value)) retry = Number(value)
  }

  const feed = (chunk: string) => {
    buffer += chunk
    let newlineIndex = buffer.indexOf('\n')
    while (newlineIndex !== -1) {
      processLine(buffer.slice(0, newlineIndex))
      buffer = buffer.slice(newlineIndex + 1)
      newlineIndex = buffer.indexOf('\n')
    }
  }

  const flush = () => {
    if (buffer.length > 0) processLine(buffer)
    buffer = ''
    dispatch()
  }

  const reset = () => {
    buffer = ''
    dataLines = []
    eventName = undefined
    eventId = undefined
    retry = undefined
  }

  return { feed, flush, reset }
}
