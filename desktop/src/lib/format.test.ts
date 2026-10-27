import { describe, expect, it } from 'vitest'
import { parseUtc, pluralize, relativeTime, safeHttpUrl } from './format'

const NOW = Date.UTC(2026, 9, 26, 12, 0, 0)

describe('time formatting', () => {
  it('parses SQLite UTC timestamps (no zone) and ISO with Z the same way', () => {
    expect(parseUtc('2026-10-26 11:00:00')?.toISOString()).toBe('2026-10-26T11:00:00.000Z')
    expect(parseUtc('2026-10-26T11:00:00.250Z')?.toISOString()).toBe('2026-10-26T11:00:00.250Z')
    expect(parseUtc('2026-10-26T11:00:00+00:00')?.toISOString()).toBe('2026-10-26T11:00:00.000Z')
  })

  it('returns null for empty or invalid input', () => {
    expect(parseUtc(null)).toBeNull()
    expect(parseUtc('')).toBeNull()
    expect(parseUtc('not a date')).toBeNull()
    expect(relativeTime('garbage', NOW)).toBe('')
  })

  it.each([
    ['2026-10-26 11:59:30', 'just now'], ['2026-10-26 11:50:00', '10m ago'], ['2026-10-26 09:00:00', '3h ago'],
    ['2026-10-24 12:00:00', '2d ago'],
  ])('relativeTime(%s) = %s', (ts, expected) => expect(relativeTime(ts, NOW)).toBe(expected))

  it('falls back to a date for old timestamps', () => {
    expect(relativeTime('2026-01-05 12:00:00', NOW)).toMatch(/Jan/)
  })

  it('pluralizes with thousands separators', () => {
    expect(pluralize(1, 'lead')).toBe('1 lead')
    expect(pluralize(0, 'lead')).toBe('0 leads')
    expect(pluralize(1200, 'site')).toBe('1,200 sites')
    expect(pluralize(2, 'address', 'addresses')).toBe('2 addresses')
  })
})

describe('safeHttpUrl', () => {
  it('accepts http and https only', () => {
    expect(safeHttpUrl('https://acme.ai/team')?.hostname).toBe('acme.ai')
    expect(safeHttpUrl('http://acme.ai')?.protocol).toBe('http:')
    for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'n/a', '', null, undefined, 'ftp://x.com']) {
      expect(safeHttpUrl(bad)).toBeNull()
    }
  })
})
