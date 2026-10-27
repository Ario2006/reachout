import { beforeEach, describe, expect, it } from 'vitest'
import { num, useLiveRuns } from './runs-store'

const s = () => useLiveRuns.getState()

describe('live run store', () => {
  beforeEach(() => useLiveRuns.setState({ runs: {}, leadTick: 0 }))

  it('tracks run state and stats from events', () => {
    s().applyRun({ run_id: 1, state: 'running', stage: 'crawling', stats: { pages: 4 } })
    expect(s().runs[1]).toMatchObject({ state: 'running', stage: 'crawling', stats: { pages: 4 } })
    s().applyRun({ run_id: 1, state: 'succeeded', stage: 'done', stats: { pages: 9 } })
    expect(num(s().runs[1]?.stats ?? {}, 'pages')).toBe(9)
  })

  it('keeps domains in arrival order and updates them in place', () => {
    s().applyDomain({ run_id: 1, domain: 'a.com', state: 'crawling', pages: 0, emails: 0, leads: 0 })
    s().applyDomain({ run_id: 1, domain: 'b.com', state: 'crawling', pages: 0, emails: 0, leads: 0 })
    s().applyDomain({ run_id: 1, domain: 'a.com', state: 'done', pages: 3, emails: 2, leads: 1, note: null })
    const r = s().runs[1]
    expect(r?.order).toEqual(['a.com', 'b.com'])
    expect(r?.domains['a.com']).toMatchObject({ state: 'done', pages: 3, leads: 1, note: '' })
  })

  it('caps the log so a long run cannot grow memory without bound', () => {
    for (let i = 0; i < 1000; i++) s().applyLog({ run_id: 1, message: `line ${i}` })
    const log = s().runs[1]?.log ?? []
    expect(log).toHaveLength(400)
    expect(log[log.length - 1]).toBe('line 999')
  })

  it('seeding from the server never overwrites newer live data', () => {
    s().applyLog({ run_id: 1, message: 'live line' })
    s().applyDomain({ run_id: 1, domain: 'a.com', state: 'done', pages: 5, emails: 1, leads: 1 })
    s().seed(1, {
      state: 'running', stage: 'crawling', stats: { pages: 1, queries_total: 4 },
      domains: [{ domain: 'a.com', state: 'crawling', pages: 1, emails: 0, leads: 0, note: '' }, { domain: 'z.com', state: 'queued', pages: 0, emails: 0, leads: 0, note: '' }],
      log: ['old line'],
    })
    const r = s().runs[1]
    expect(r?.log).toEqual(['live line'])
    expect(r?.domains['a.com']?.state).toBe('done')
    expect(r?.domains['z.com']?.state).toBe('queued')
    expect(r?.order).toEqual(['a.com', 'z.com'])
    expect(num(r?.stats ?? {}, 'queries_total')).toBe(4)
  })

  it('seeds an unseen run with the server log', () => {
    s().seed(2, { state: 'succeeded', stage: 'done', stats: {}, domains: [], log: ['a', 'b'] })
    expect(s().runs[2]?.log).toEqual(['a', 'b'])
  })

  it('bumps a counter whenever leads change', () => {
    s().bumpLeads(); s().bumpLeads()
    expect(s().leadTick).toBe(2)
  })

  it('num() is safe on missing and non-numeric values', () => {
    expect(num({}, 'x')).toBe(0)
    expect(num({ x: 'abc' }, 'x')).toBe(0)
    expect(num({ x: 5 }, 'x')).toBe(5)
  })
})
