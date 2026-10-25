import { describe, expect, it, vi } from 'vitest'
import { createSseParser, streamEvents, type SseMessage, type StreamState } from './sse'

function parse(chunks: string[]): SseMessage[] {
  const out: SseMessage[] = []
  const p = createSseParser((m) => out.push(m))
  for (const c of chunks) p.push(c)
  return out
}

describe('createSseParser', () => {
  it('parses a complete message with id and event', () => {
    expect(parse(['id: 7\nevent: job\ndata: {"a":1}\n\n'])).toEqual([{ id: '7', event: 'job', data: '{"a":1}' }])
  })

  it('handles chunk boundaries anywhere, even inside a field name', () => {
    const msgs = parse(['id: 1\neve', 'nt: job\nda', 'ta: hel', 'lo\n', '\nid: 2\ndata: x\n\n'])
    expect(msgs).toEqual([
      { id: '1', event: 'job', data: 'hello' },
      { id: '2', event: 'message', data: 'x' },
    ])
  })

  it('joins multi-line data with newlines', () => {
    expect(parse(['data: a\ndata: b\n\n'])[0]?.data).toBe('a\nb')
  })

  it('ignores comments, retry hints and blocks without data', () => {
    expect(parse(['retry: 2000\n: connected\n\n: keep-alive\n\n'])).toEqual([])
  })

  it('supports CRLF, including a CR/LF pair split across chunks', () => {
    expect(parse(['data: a\r\n\r\n'])).toHaveLength(1)
    expect(parse(['data: a\r', '\n\r', '\n'])).toEqual([{ id: null, event: 'message', data: 'a' }])
  })

  it('does not dispatch a partial message until the blank line arrives', () => {
    expect(parse(['data: partial\n'])).toEqual([])
  })

  it('keeps the last id for later messages that omit it', () => {
    const msgs = parse(['id: 5\ndata: a\n\ndata: b\n\n'])
    expect(msgs.map((m) => m.id)).toEqual(['5', '5'])
  })

  it('treats a value with no leading space correctly', () => {
    expect(parse(['data:no-space\n\n'])[0]?.data).toBe('no-space')
  })
})

function streamResponse(parts: string[]): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p))
      c.close()
    },
  })
  return new Response(body, { status: 200 })
}

describe('streamEvents', () => {
  it('sends the bearer token, parses messages, and resumes with Last-Event-ID after a drop', async () => {
    const ac = new AbortController()
    const seen: SseMessage[] = []
    const states: StreamState[] = []
    const calls: Headers[] = []
    let n = 0
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      calls.push(new Headers(init?.headers))
      n += 1
      if (n === 1) return streamResponse(['id: 1\nevent: job\ndata: a\n\n', 'id: 2\nevent: job\ndata: b\n\n'])
      queueMicrotask(() => ac.abort())
      return streamResponse(['id: 3\nevent: job\ndata: c\n\n'])
    }) as unknown as typeof fetch

    await streamEvents({
      baseUrl: 'http://x', token: 'tok', signal: ac.signal, fetchImpl, backoffMs: () => 1,
      onMessage: (m) => seen.push(m), onState: (s) => states.push(s),
    })

    expect(seen.map((m) => m.data)).toEqual(['a', 'b', 'c'])
    expect(calls[0]?.get('Authorization')).toBe('Bearer tok')
    expect(calls[0]?.get('Last-Event-ID')).toBeNull()
    expect(calls[1]?.get('Last-Event-ID')).toBe('2')
    expect(states).toContain('open')
    expect(states).toContain('reconnecting')
  })

  it('keeps retrying after an HTTP error until aborted', async () => {
    const ac = new AbortController()
    let n = 0
    const fetchImpl = vi.fn(async () => {
      n += 1
      if (n === 3) ac.abort()
      return new Response('nope', { status: 401 })
    }) as unknown as typeof fetch
    await streamEvents({ baseUrl: 'http://x', token: 't', signal: ac.signal, fetchImpl, backoffMs: () => 1, onMessage: () => {}, onState: () => {} })
    expect(n).toBe(3)
  })

  it('returns promptly when aborted before connecting', async () => {
    const ac = new AbortController()
    ac.abort()
    const fetchImpl = vi.fn() as unknown as typeof fetch
    await streamEvents({ baseUrl: 'http://x', token: 't', signal: ac.signal, fetchImpl, onMessage: () => {}, onState: () => {} })
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
