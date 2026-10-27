import { Loader2, Search } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { MODE_OPTIONS } from '@/components/lead-badges'
import { ApiError } from '@/lib/api/client'
import { usePlan, useRuns, useStartRun } from '@/lib/api/hooks'
import type { Mode, Plan, PlanResponse } from '@/lib/api/types'
import { relativeTime } from '@/lib/format'
import { PlanCard, type RunOptionsState } from './plan-card'
import { RunProgress } from './run-progress'

const EXAMPLES = [
  'Software/AI internships at seed-stage startups in Bangalore',
  'Pre-seed investors who back AI developer tools in India/SEA',
  'Founders of D2C brands who need a website redesign',
]

export function DiscoverPage() {
  const [request, setRequest] = useState('')
  const [mode, setMode] = useState<Mode | null>(null)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [options, setOptions] = useState<RunOptionsState>({ recrawl: false, use_overpass: false, expand_lists: true })
  const [selected, setSelected] = useState<number | null>(null)

  const runs = useRuns()
  const makePlan = usePlan()
  const startRun = useStartRun()
  const activeRun = runs.data?.find((r) => r.state === 'queued' || r.state === 'running')
  const shown = selected ?? activeRun?.id ?? null

  function generate() {
    makePlan.mutate({ request: request.trim(), ...(mode ? { mode } : {}) }, {
      onSuccess: (p) => setPlan(p),
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not make a plan'),
    })
  }

  function run() {
    if (!plan) return
    startRun.mutate({ request_text: request.trim(), plan: plan.plan, options }, {
      onSuccess: (r) => { setSelected(r.id); setPlan(null) },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not start the search'),
    })
  }

  const setPlanBody = (p: Plan) => setPlan((cur) => (cur ? { ...cur, plan: p } : cur))

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 px-8 pt-2 pb-16">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="text-muted-foreground text-sm">Describe who you want to reach. You will review the plan before anything runs.</p>
      </div>

      <section aria-label="New search" className="space-y-3">
        <Textarea
          aria-label="Who do you want to reach?"
          rows={3}
          value={request}
          disabled={Boolean(activeRun)}
          placeholder="Describe who you want to reach…"
          className="resize-none text-base"
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && request.trim()) generate() }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Mode" className="flex gap-1.5">
            <Button size="sm" variant={mode === null ? 'default' : 'outline'} aria-pressed={mode === null} onClick={() => setMode(null)}>Auto</Button>
            {MODE_OPTIONS.map((m) => (
              <Button key={m.value} size="sm" variant={mode === m.value ? 'default' : 'outline'} aria-pressed={mode === m.value} onClick={() => setMode(m.value)}>
                {m.label}
              </Button>
            ))}
          </div>
          <Button onClick={generate} disabled={!request.trim() || makePlan.isPending || Boolean(activeRun)} className="ml-auto">
            {makePlan.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Search aria-hidden />}
            {makePlan.isPending ? 'Planning…' : 'Make a plan'}
          </Button>
        </div>
        {request === '' && !plan ? (
          <div className="flex flex-wrap gap-1.5" aria-label="Examples">
            {EXAMPLES.map((ex) => (
              <Button key={ex} size="xs" variant="ghost" className="text-muted-foreground h-auto py-1 whitespace-normal" onClick={() => setRequest(ex)}>{ex}</Button>
            ))}
          </div>
        ) : null}
      </section>

      {plan ? (
        <PlanCard plan={plan.plan} source={plan.source} note={plan.note} onChange={setPlanBody} options={options}
          onOptions={setOptions} onRun={run} running={startRun.isPending} disabled={Boolean(activeRun)} />
      ) : null}

      {shown !== null ? <RunProgress key={shown} runId={shown} /> : null}

      {runs.data && runs.data.length > 0 ? (
        <section aria-label="Past searches" className="space-y-2">
          <h2 className="text-sm font-medium">Past searches</h2>
          <ul className="divide-y rounded-lg border">
            {runs.data.slice(0, 8).map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => setSelected(r.id)} aria-current={shown === r.id ? 'true' : undefined}
                  className="hover:bg-muted/50 flex w-full items-center gap-3 px-3 py-2 text-left text-sm outline-none focus-visible:ring-2">
                  <span className="min-w-0 flex-1 truncate">{r.request_text}</span>
                  <Badge variant="outline" className="capitalize">{r.state}</Badge>
                  <span className="text-muted-foreground w-24 text-right text-xs tabular-nums">{r.leads_found} leads</span>
                  <span className="text-muted-foreground w-20 text-right text-xs">{relativeTime(r.started_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
