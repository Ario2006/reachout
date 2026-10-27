import { AlertCircle, AlertTriangle, ArrowRight, Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useActivity, useMetrics, type MetricsRange } from '@/lib/api/hooks-inbox'
import type { ActivityItem, AttentionItem, Metrics } from '@/lib/api/types'
import { relativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { FunnelChart, SendsChart } from './charts'

const RANGES: { value: MetricsRange; label: string }[] = [
  { value: '7d', label: '7 days' }, { value: '30d', label: '30 days' }, { value: 'all', label: 'All time' },
]

const pct = (v: number | null | undefined, empty: string): string => (v === null || v === undefined ? empty : `${v.toFixed(1)}%`)

function Kpi({ label, value, hint, testId }: { label: string; value: ReactNode; hint?: string; testId: string }) {
  return (
    <Card size="sm" data-testid={testId}>
      <CardContent className="space-y-1">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </CardContent>
    </Card>
  )
}

function KpiRow({ k }: { k: Metrics['kpis'] }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Kpi testId="kpi-leads" label="Leads" value={k.leads.toLocaleString()} hint={`${k.sent_total.toLocaleString()} emailed`} />
      <Kpi testId="kpi-drafts" label="Drafts to review" value={k.drafts_awaiting_review.toLocaleString()} />
      <Kpi testId="kpi-today" label="Sent today" value={<>{k.sent_today}<span className="text-muted-foreground text-base font-normal"> / {k.daily_cap}</span></>} />
      <Kpi testId="kpi-replies" label="Replies" value={k.replies.toLocaleString()} hint={`${k.opted_out} opted out`} />
      <Kpi testId="kpi-reply-rate" label="Reply rate" value={pct(k.reply_rate, '–')} hint={k.reply_rate === null ? 'Needs delivered emails' : `of ${k.delivered.toLocaleString()} delivered`} />
      <Kpi testId="kpi-bounce-rate" label="Bounce rate" value={pct(k.bounce_rate, '–')} hint={k.bounce_rate === null ? 'Not enough sends yet' : `${k.bounced} bounced`} />
    </div>
  )
}

const SEVERITY = {
  info: { icon: Info, cls: 'text-muted-foreground' },
  warning: { icon: AlertTriangle, cls: 'text-amber-700 dark:text-amber-400' },
  critical: { icon: AlertCircle, cls: 'text-destructive' },
} as const

function Attention({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) return <p className="text-muted-foreground text-sm" data-testid="attention-empty">Nothing needs your attention.</p>
  return (
    <ul className="divide-y" data-testid="attention">
      {items.map((a) => {
        const S = SEVERITY[a.severity]
        return (
          <li key={a.kind}>
            <Link to={a.link} className="hover:bg-muted/60 focus-visible:ring-ring flex items-start gap-2 rounded-md px-1 py-2 text-sm outline-none focus-visible:ring-2">
              <S.icon className={cn('mt-0.5 size-4 shrink-0', S.cls)} aria-label={a.severity} />
              <span className="flex-1">{a.message}</span>
              <ArrowRight className="text-muted-foreground mt-0.5 size-4" aria-hidden />
            </Link>
          </li>
        )
      })}
    </ul>
  )
}

function Feed({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return <p className="text-muted-foreground text-sm" data-testid="feed-empty">No activity yet. Sends, replies and bounces appear here as they happen.</p>
  return (
    <ul className="space-y-2" data-testid="feed" aria-live="polite">
      {items.map((a, i) => (
        <li key={`${a.at}-${a.kind}-${i}`} className="flex items-baseline gap-2 text-sm">
          <span className={cn('size-1.5 shrink-0 translate-y-[-1px] rounded-full', a.kind === 'bounce' ? 'bg-destructive' : a.kind === 'reply' ? 'bg-primary' : 'bg-muted-foreground/50')} aria-hidden />
          <span className="flex-1">{a.lead_id ? <Link to={`/inbox?lead=${a.lead_id}`} className="hover:underline">{a.text}</Link> : a.text}</span>
          <span className="text-muted-foreground shrink-0 text-xs">{relativeTime(a.at)}</span>
        </li>
      ))}
    </ul>
  )
}

function ErrorBox({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  return (
    <div role="alert" className="space-y-2 text-sm">
      <p className="text-destructive">Could not load {what}. {error instanceof ApiError ? error.detail : ''}</p>
      <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
    </div>
  )
}

export function DashboardPage() {
  const [range, setRange] = useState<MetricsRange>('30d')
  const metrics = useMetrics(range)
  const activity = useActivity(30)

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <div className="flex gap-1" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <Button key={r.value} size="sm" variant={range === r.value ? 'default' : 'outline'} aria-pressed={range === r.value} onClick={() => setRange(r.value)}>{r.label}</Button>
          ))}
        </div>
      </header>

      {metrics.isPending ? (
        <div className="space-y-4" role="status" aria-label="Loading metrics">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-20" />)}</div>
          <Skeleton className="h-52" />
        </div>
      ) : metrics.isError ? (
        <ErrorBox what="the numbers" error={metrics.error} onRetry={() => void metrics.refetch()} />
      ) : (
        <>
          <KpiRow k={metrics.data.kpis} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader><CardTitle>Funnel</CardTitle></CardHeader><CardContent><FunnelChart funnel={metrics.data.funnel} /></CardContent></Card>
            <Card><CardHeader><CardTitle>Emails sent per day</CardTitle></CardHeader><CardContent><SendsChart days={metrics.data.per_day} /></CardContent></Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader><CardTitle>Needs attention</CardTitle></CardHeader><CardContent><Attention items={metrics.data.attention} /></CardContent></Card>
            <Card>
              <CardHeader><CardTitle>Recent activity</CardTitle></CardHeader>
              <CardContent>
                {activity.isPending ? <div className="space-y-2" role="status" aria-label="Loading activity">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}</div>
                  : activity.isError ? <ErrorBox what="the activity feed" error={activity.error} onRetry={() => void activity.refetch()} />
                    : <Feed items={activity.data} />}
              </CardContent>
            </Card>
          </div>
          <p className="text-muted-foreground text-xs" data-testid="opens-note">{metrics.data.opens_note}</p>
        </>
      )}
    </div>
  )
}
