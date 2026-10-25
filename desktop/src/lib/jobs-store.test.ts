import { describe, expect, it } from 'vitest'
import { toJobEvent, TERMINAL, useLive } from './jobs-store'

describe('jobs store', () => {
  it('normalises REST jobs and rejects unknown states', () => {
    const ok = toJobEvent({ id: 1, kind: 'pull_model', state: 'running', progress: 5, total: 100 })
    expect(ok).toEqual({ id: 1, kind: 'pull_model', state: 'running', progress: 5, total: 100, message: null, error: null })
    expect(toJobEvent({ id: 1, kind: 'k', state: 'exploded', progress: 0, total: 0 })).toBeNull()
  })

  it('upserts by id and classifies terminal states', () => {
    useLive.getState().upsertJob({ id: 9, kind: 'k', state: 'running', progress: 1, total: 2, message: null, error: null })
    useLive.getState().upsertJob({ id: 9, kind: 'k', state: 'failed', progress: 1, total: 2, message: null, error: 'x' })
    const j = useLive.getState().jobs[9]
    expect(j?.state).toBe('failed')
    expect(TERMINAL.has('failed')).toBe(true)
    expect(TERMINAL.has('running')).toBe(false)
  })
})
