import { Check, CircleDashed, Loader2, RotateCw, X } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { downloadLeadsCsv, EMPTY_QUERY, useCancelRun, useResumeRun, useRun } from '@/lib/api/hooks'
import { pluralize } from '@/lib/format'
import { num, useLiveRuns, type LiveDomain } from '@/lib/runs-store'
import { cn } from '@/lib/utils'
import { EngineStats } from './engine-stats'

type StepState = 'pending' | 'active' | 'done'

interface Step { id: string; label: string; state: StepState; detail: string }

/** The four phases overlap (each site is crawled, extracted and validated as one unit), so each
 *  step is derived from the counters rather than from a single "current stage". */
export function deriveSteps(stage: string, state: string, s: Record<string, unknown>): Step[] {
  const finished = stage === 'done' || state === 'succeeded' || state === 'cancelled' || state === 'failed'
  const crawling = stage === 'crawling' && !finished
  const done = finished
  const st = (active: boolean, complete: boolean): StepState => (complete ? 'done' : active ? 'active' : 'pending')
  const searchDone = stage !== 'searching' || finished
  return [
    { id: 'search', label: 'Searching', state: st(stage === 'searching' && !finished, searchDone),
      detail: `${num(s, 'queries_done')}/${num(s, 'queries_total')} queries · ${num(s, 'search_hits').toLocaleString()} results` },
    { id: 'crawl', label: 'Crawling', state: st(crawling, done),
      detail: `${num(s, 'domains_done')}/${num(s, 'domains_total')} sites · ${num(s, 'pages').toLocaleString()} pages` },
    { id: 'extract', label: 'Extracting', state: st(crawling && num(s, 'pages') > 0, done && num(s, 'pages') > 0),
      detail: `${num(s, 'emails_found').toLocaleString()} emails · ${num(s, 'people_found').toLocaleString()} people` },
    { id: 'validate', label: 'Validating', state: st(crawling && num(s, 'leads_checked') > 0, done && num(s, 'leads_checked') > 0),
      detail: `${num(s, 'leads_new').toLocaleString()} kept · ${num(s, 'leads_dropped').toLocaleString()} dropped (no mail server)` },
  ]
}

const STATE_LABEL: Record<string, string> = { queued: 'Waiting', crawling: 'Crawling', done: 'Done', skipped: 'Skipped', failed: 'Failed' }

export function RunProgress({ runId }: { runId: number }) {
  const detail = useRun(runId)
  const cancel = useCancelRun()
  const resume = useResumeRun()
  const seed = useLiveRuns((s) => s.seed)
  const live = useLiveRuns((s) => s.runs[runId])
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const d = detail.data
    if (!d) return
    seed(runId, {
      state: d.state, stage: d.stage ?? undefined, stats: d.stats,
      domains: d.domains.map((x): LiveDomain => ({ domain: x.domain, state: x.state, pages: x.pages, emails: x.emails, leads: x.leads, note: x.note ?? '' })),
      log: d.log.map((l) => l.message),
    })
  }, [detail.data, runId, seed])

  const run = detail.data
  const state = live?.state ?? run?.state ?? 'queued'
  const stage = live?.stage ?? run?.stage ?? 'searching'
  const stats = live?.stats ?? run?.stats ?? {}
  const active = state === 'queued' || state === 'running'
  const steps = useMemo(() => deriveSteps(stage, state, stats), [stage, state, stats])
  const rows = (live?.order ?? []).map((d) => live?.domains[d]).filter((d): d is LiveDomain => d !== undefined)
  const logLines = live?.log ?? []

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logLines.length])

  if (detail.isPending) return <Skeleton className="h-64" role="status" aria-label="Loading run" />
  if (detail.isError) {
    return (
      <div role="alert" className="space-y-2 rounded-lg border p-4 text-sm">
        <p className="text-destructive">Could not load this search. {detail.error instanceof ApiError ? detail.error.detail : ''}</p>
        <Button size="sm" variant="outline" onClick={() => void detail.refetch()}>Retry</Button>
      </div>
    )
  }
  if (!run) return null

  const total = Math.max(num(stats, 'domains_total'), 1)
  const pct = stage === 'searching' ? (num(stats, 'queries_done') / Math.max(num(stats, 'queries_total'), 1)) * 25 : 25 + (num(stats, 'domains_done') / total) * 75
  const byConf = (stats['by_confidence'] ?? {}) as Record<string, number>

  return (
    <Card data-testid="run-progress">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate">{run.request_text}</CardTitle>
            <p className="text-muted-foreground text-xs">Run #{run.id} · {run.mode}</p>
          </div>
          {active ? (
            <Button variant="outline" size="sm" onClick={() => cancel.mutate(runId)} disabled={cancel.isPending}>
              <X aria-hidden /> Cancel
            </Button>
          ) : (
            <Badge variant={state === 'succeeded' ? 'default' : 'outline'} className="capitalize">{state}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <ol className="grid gap-2 sm:grid-cols-4" aria-label="Progress">
          {steps.map((s) => (
            <li key={s.id} className={cn('rounded-lg border p-3', s.state === 'active' && 'border-primary/50 bg-primary/5')} data-state={s.state}>
              <div className="flex items-center gap-1.5 text-sm font-medium">
                {s.state === 'done' ? <Check className="text-emerald-600 size-4" aria-label="done" />
                  : s.state === 'active' ? <Loader2 className="text-primary size-4 animate-spin" aria-label="in progress" />
                    : <CircleDashed className="text-muted-foreground size-4" aria-label="waiting" />}
                {s.label}
              </div>
              <p className="text-muted-foreground mt-1 text-xs">{s.detail}</p>
            </li>
          ))}
        </ol>
        <Progress value={state === 'succeeded' ? 100 : Math.round(pct)} aria-label="Overall progress" />
        {/* Announces stage changes and the outcome only, not every counter tick. */}
        <p className="sr-only" role="status" data-testid="run-announce">
          {active ? `Search in progress: ${stage}.` : `Search ${state}. ${pluralize(num(stats, 'leads_new'), 'new lead')}.`}
        </p>
        <p className="text-muted-foreground text-xs">Sites are crawled, read and checked one by one, so these steps overlap. Leads appear below as soon as each site is done.</p>

        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1" data-testid="lead-counters">
          <span className="text-2xl font-semibold tabular-nums">{num(stats, 'leads_new').toLocaleString()}</span>
          <span className="text-muted-foreground text-sm">
            new leads
            {byConf['published_personal'] !== undefined || byConf['published_generic'] !== undefined || byConf['guessed'] !== undefined
              ? ` (${byConf['published_personal'] ?? 0} personal, ${byConf['published_generic'] ?? 0} generic, ${byConf['guessed'] ?? 0} guessed)`
              : ''}
          </span>
        </div>

        {run.error ? <p className="text-destructive text-sm" role="alert">{run.error}</p> : null}

        {!active ? (
          <div className="flex flex-wrap gap-2 rounded-lg border p-3" data-testid="run-summary">
            <p className="flex-1 text-sm">
              {state === 'cancelled' ? 'Cancelled. ' : state === 'failed' ? 'Failed. ' : 'Finished. '}
              {pluralize(num(stats, 'leads_new'), 'new lead')} from {pluralize(num(stats, 'domains_done'), 'site')}
              {num(stats, 'leads_dup') > 0 ? `, ${num(stats, 'leads_dup')} already known` : ''}
              {num(stats, 'leads_suppressed') > 0 ? `, ${num(stats, 'leads_suppressed')} suppressed` : ''}.
            </p>
            {state === 'cancelled' || state === 'failed' ? (
              <Button size="sm" variant="outline" onClick={() => resume.mutate(runId)} disabled={resume.isPending}>
                <RotateCw aria-hidden /> Resume
              </Button>
            ) : null}
            <Button asChild size="sm"><Link to={`/leads?run=${run.id}`}>Review {num(stats, 'leads_new')} new leads</Link></Button>
            <Button size="sm" variant="outline" onClick={() => void downloadLeadsCsv({ ...EMPTY_QUERY, run_id: run.id }, `leads-run-${run.id}.csv`)}>
              Download CSV
            </Button>
          </div>
        ) : null}

        <EngineStats stats={stats} />

        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-1.5 text-sm font-medium">Log</h3>
            <div ref={logRef} className="bg-muted/40 h-56 overflow-y-auto rounded-lg border p-2 font-mono text-xs leading-relaxed" role="log" aria-label="Run log" aria-live="off" data-testid="run-log">
              {logLines.length === 0 ? <span className="text-muted-foreground">Waiting for the worker…</span> : logLines.map((l, i) => <div key={i} className="break-words">{l}</div>)}
            </div>
          </div>
          <div>
            <h3 className="mb-1.5 text-sm font-medium">Sites {rows.length > 0 ? `(${rows.length})` : ''}</h3>
            <div className="h-56 overflow-y-auto rounded-lg border">
              <Table>
                <TableHeader className="bg-background sticky top-0">
                  <TableRow><TableHead>Site</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Pages</TableHead><TableHead className="text-right">Leads</TableHead></TableRow>
                </TableHeader>
                <TableBody>
                  {rows.length === 0 ? (
                    <TableRow><TableCell colSpan={4} className="text-muted-foreground text-center text-xs">Sites appear once the search finishes.</TableCell></TableRow>
                  ) : rows.map((d) => (
                    <TableRow key={d.domain} data-testid={`domain-${d.domain}`}>
                      <TableCell className="max-w-40 truncate font-mono text-xs" title={d.note || d.domain}>{d.domain}</TableCell>
                      <TableCell className="text-xs">{STATE_LABEL[d.state] ?? d.state}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{d.pages}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{d.leads}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
