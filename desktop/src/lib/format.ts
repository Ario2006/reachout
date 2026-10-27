/** SQLite gives "YYYY-MM-DD HH:MM:SS" (UTC, no zone); the app gives ISO with a Z. Parse both as UTC. */
export function parseUtc(ts: string | null | undefined): Date | null {
  if (!ts) return null
  const iso = ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(d.getTime()) ? null : d
}

export function relativeTime(ts: string | null | undefined, now: number = Date.now()): string {
  const d = parseUtc(ts)
  if (!d) return ''
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const days = Math.round(h / 24)
  if (days < 30) return `${days}d ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date(now).getFullYear() ? undefined : 'numeric' })
}

export const pluralize = (n: number, one: string, many = one + 's'): string => `${n.toLocaleString()} ${n === 1 ? one : many}`

/** An http(s) URL we are willing to link to, or null. Source URLs can come from an imported CSV. */
export function safeHttpUrl(raw: string | null | undefined): URL | null {
  if (!raw) return null
  try {
    const u = new URL(raw)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u : null
  } catch {
    return null
  }
}
