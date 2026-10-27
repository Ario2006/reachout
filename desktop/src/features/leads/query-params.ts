import type { SortingState } from '@tanstack/react-table'
import { EMPTY_QUERY, type LeadQuery } from '@/lib/api/hooks'
import type { EmailConfidence, LeadStatus, Mode } from '@/lib/api/types'

const STATUSES: readonly LeadStatus[] = ['new', 'drafted', 'approved', 'queued', 'sent', 'replied', 'bounced', 'opted_out', 'auto_reply', 'closed']
const MODES: readonly Mode[] = ['internship', 'investor', 'service']
const CONFS: readonly EmailConfidence[] = ['published_personal', 'published_generic', 'guessed']

/** Sort keys the backend accepts, and the columns that carry them. */
export const SORTABLE = ['name', 'email', 'role', 'company', 'domain', 'status', 'updated_at'] as const

function pick<T extends string>(raw: string | null, allowed: readonly T[]): T[] {
  return (raw ?? '').split(',').filter((v): v is T => (allowed as readonly string[]).includes(v))
}

export function queryFromParams(sp: URLSearchParams): LeadQuery {
  const run = Number(sp.get('run'))
  return {
    ...EMPTY_QUERY,
    q: sp.get('q') ?? '',
    status: pick(sp.get('status'), STATUSES),
    mode: pick(sp.get('mode'), MODES),
    confidence: pick(sp.get('conf'), CONFS),
    run_id: Number.isInteger(run) && run > 0 ? run : null,
    sort: formatSort(parseSort(sp.get('sort') ?? '')),
  }
}

export function paramsFromQuery(q: LeadQuery): URLSearchParams {
  const sp = new URLSearchParams()
  if (q.q) sp.set('q', q.q)
  if (q.status.length) sp.set('status', q.status.join(','))
  if (q.mode.length) sp.set('mode', q.mode.join(','))
  if (q.confidence.length) sp.set('conf', q.confidence.join(','))
  if (q.run_id !== null) sp.set('run', String(q.run_id))
  if (q.sort) sp.set('sort', q.sort)
  return sp
}

/** "company:asc,name:desc" -> TanStack sorting state (unknown columns are dropped). */
export function parseSort(sort: string): SortingState {
  const out: SortingState = []
  for (const part of sort.split(',')) {
    const [id, dir] = part.trim().split(':')
    if (id && (SORTABLE as readonly string[]).includes(id) && !out.some((s) => s.id === id)) out.push({ id, desc: dir === 'desc' })
  }
  return out
}

export const formatSort = (s: SortingState): string => s.map((x) => `${x.id}:${x.desc ? 'desc' : 'asc'}`).join(',')

export const hasFilters = (q: LeadQuery): boolean =>
  q.q !== '' || q.status.length > 0 || q.mode.length > 0 || q.confidence.length > 0 || q.run_id !== null
