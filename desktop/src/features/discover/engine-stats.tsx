import { num } from '@/lib/runs-store'

type Obj = Record<string, unknown>
const obj = (v: unknown): Obj => (v !== null && typeof v === 'object' ? (v as Obj) : {})

const FAIL_LABEL: Record<string, string> = {
  robots: 'blocked by robots.txt', http_404: 'page not found', timeout: 'timed out', dns: 'domain does not exist',
  blocked: 'bot protection', rate_limited: 'rate limited', breaker: 'site skipped after repeated failures', too_large: 'page too large',
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-2.5" title={hint}>
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
    </div>
  )
}

/** How the crawl went: where pages came from, what failed, where time was spent. Shown once a run has fetched anything. */
export function EngineStats({ stats }: { stats: Obj }) {
  const e = obj(stats['engine'])
  if (Object.keys(e).length === 0) return null
  const tiers = obj(e['tiers'])
  const llm = obj(e['llm'])
  const stages = obj(e['stage_seconds'])
  const failures = Object.entries(obj(e['failures'])).filter(([, n]) => typeof n === 'number' && n > 0) as [string, number][]
  const pct = (x: number) => `${Math.round(x * 100)}%`
  const secs = (x: number) => (x >= 60 ? `${(x / 60).toFixed(1)} min` : `${x.toFixed(1)} s`)
  return (
    <section aria-label="Crawl statistics" data-testid="engine-stats" className="space-y-2">
      <h3 className="text-sm font-medium">How the crawl went</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Pages per second" value={num(e, 'pages_per_sec').toFixed(1)} />
        <Stat label="From saved pages" value={pct(num(e, 'cache_hit_rate'))} hint="A repeat search reuses pages fetched within the cache period." />
        <Stat label="Fetched live" value={`${num(tiers, 'http')} web · ${num(tiers, 'browser')} browser`} hint="Browser = JavaScript-rendered pages" />
        <Stat label="Memory (peak)" value={`${Math.round(num(e, 'peak_rss_mb'))} MB`} />
        <Stat label="Model calls" value={String(num(llm, 'calls'))} hint="Pages whose people list could not be read from the page structure" />
        <Stat label="Pages per model call" value={num(llm, 'pages_per_call').toFixed(1)} />
        <Stat label="Retries" value={String(num(e, 'retries'))} />
        <Stat label="Stopped early" value={`${num(e, 'early_stops')} sites`} hint="Sites that already had enough contacts" />
      </div>
      {failures.length > 0 ? (
        <p className="text-muted-foreground text-xs">
          Not fetched: {failures.map(([k, n]) => `${n} ${FAIL_LABEL[k] ?? k}`).join(', ')}.
        </p>
      ) : null}
      {Object.keys(stages).length > 0 ? (
        <p className="text-muted-foreground text-xs">
          Time by stage: {Object.entries(stages).map(([k, v]) => `${k} ${secs(typeof v === 'number' ? v : 0)}`).join(' · ')}
        </p>
      ) : null}
    </section>
  )
}
