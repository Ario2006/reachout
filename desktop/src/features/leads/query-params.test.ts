import { describe, expect, it } from 'vitest'
import { EMPTY_QUERY } from '@/lib/api/hooks'
import { formatSort, hasFilters, paramsFromQuery, parseSort, queryFromParams } from './query-params'

const parse = (s: string) => queryFromParams(new URLSearchParams(s))

describe('lead query <-> URL params', () => {
  it('round-trips every filter', () => {
    const q = { q: 'acme', status: ['new', 'sent'], mode: ['investor'], confidence: ['guessed'], run_id: 7, sort: 'company:asc,name:desc' } as const
    const back = queryFromParams(paramsFromQuery({ ...q, status: [...q.status], mode: [...q.mode], confidence: [...q.confidence] }))
    expect(back).toEqual({ ...q, status: ['new', 'sent'], mode: ['investor'], confidence: ['guessed'] })
  })

  it('omits empty filters from the URL', () => {
    expect(paramsFromQuery(EMPTY_QUERY).toString()).toBe('')
  })

  it('drops unknown or malformed values instead of sending them to the API', () => {
    const q = parse('status=new,bogus&mode=x&conf=guessed,maybe&run=abc&sort=name:asc,evil:asc,name:desc')
    expect(q.status).toEqual(['new'])
    expect(q.mode).toEqual([])
    expect(q.confidence).toEqual(['guessed'])
    expect(q.run_id).toBeNull()
    expect(q.sort).toBe('name:asc')
  })

  it('rejects non-positive or fractional run ids', () => {
    for (const bad of ['0', '-3', '1.5', '']) expect(parse(`run=${bad}`).run_id).toBeNull()
    expect(parse('run=12').run_id).toBe(12)
  })

  it('parses and formats sort state, keeping order and ignoring unknown columns', () => {
    expect(parseSort('company:asc,name:desc,bogus:asc')).toEqual([{ id: 'company', desc: false }, { id: 'name', desc: true }])
    expect(formatSort([{ id: 'email', desc: true }, { id: 'role', desc: false }])).toBe('email:desc,role:asc')
    expect(parseSort('')).toEqual([])
  })

  it('knows when any filter is active (sorting does not count)', () => {
    expect(hasFilters(EMPTY_QUERY)).toBe(false)
    expect(hasFilters({ ...EMPTY_QUERY, sort: 'name:asc' })).toBe(false)
    expect(hasFilters({ ...EMPTY_QUERY, q: 'x' })).toBe(true)
    expect(hasFilters({ ...EMPTY_QUERY, run_id: 1 })).toBe(true)
  })
})
