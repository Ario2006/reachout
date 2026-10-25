export interface SseMessage {
  id: string | null
  event: string
  data: string
}

/**
 * Incremental Server-Sent Events parser. Feed it text chunks of any size; it emits one
 * message per blank-line-terminated block. Comment lines (": ...") are ignored.
 */
export function createSseParser(onMessage: (m: SseMessage) => void): { push(chunk: string): void } {
  let buffer = ''
  let id: string | null = null
  let event = ''
  let data: string[] = []

  const dispatch = (): void => {
    if (data.length > 0) onMessage({ id, event: event || 'message', data: data.join('\n') })
    event = ''
    data = []
  }

  const handleLine = (line: string): void => {
    if (line === '') return dispatch()
    if (line.startsWith(':')) return
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
    else if (field === 'id' && !value.includes('\0')) id = value
  }

  return {
    push(chunk: string): void {
      buffer += chunk
      // A trailing "\r" might be the first half of "\r\n": hold it back until we know.
      const held = buffer.endsWith('\r') ? '\r' : ''
      const body = held ? buffer.slice(0, -1) : buffer
      const lines = body.split(/\r\n|\n|\r/)
      buffer = (lines.pop() ?? '') + held
      for (const line of lines) handleLine(line)
    },
  }
}

export type StreamState = 'connecting' | 'open' | 'reconnecting'

export interface StreamOptions {
  baseUrl: string
  token: string
  signal: AbortSignal
  onMessage: (m: SseMessage) => void
  onState: (s: StreamState) => void
  /** Injectable for tests. */
  fetchImpl?: typeof fetch
  backoffMs?: (attempt: number) => number
}

/**
 * Connect to /events with a bearer header (EventSource cannot send one) and keep
 * reconnecting, resuming from the last seen event id, until the signal aborts.
 */
export async function streamEvents(opts: StreamOptions): Promise<void> {
  const doFetch = opts.fetchImpl ?? fetch
  const backoff = opts.backoffMs ?? ((n) => Math.min(1000 * 2 ** n, 10_000))
  let lastId: string | null = null
  let attempt = 0

  while (!opts.signal.aborted) {
    opts.onState(attempt === 0 ? 'connecting' : 'reconnecting')
    try {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${opts.token}`,
        Accept: 'text/event-stream',
      }
      if (lastId !== null) headers['Last-Event-ID'] = lastId
      const res = await doFetch(`${opts.baseUrl}/events`, { headers, signal: opts.signal })
      if (!res.ok || !res.body) throw new Error(`events stream failed (${res.status})`)
      opts.onState('open')
      attempt = 0
      const parser = createSseParser((m) => {
        if (m.id !== null) lastId = m.id
        opts.onMessage(m)
      })
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.push(value)
      }
    } catch {
      if (opts.signal.aborted) return
    }
    if (opts.signal.aborted) return
    opts.onState('reconnecting')
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, backoff(attempt++))
      opts.signal.addEventListener('abort', () => { clearTimeout(t); resolve() }, { once: true })
    })
  }
}
