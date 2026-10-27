import { Loader2, Play, Sparkles } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChipList } from '@/components/chip-list'
import type { Mode, Plan } from '@/lib/api/types'
import { MODE_OPTIONS } from '@/components/lead-badges'

export interface RunOptionsState {
  recrawl: boolean
  use_overpass: boolean
  expand_lists: boolean
}

interface Props {
  plan: Plan
  source: 'llm' | 'fallback'
  note: string
  onChange: (plan: Plan) => void
  options: RunOptionsState
  onOptions: (o: RunOptionsState) => void
  onRun: () => void
  running: boolean
  disabled: boolean
}

type ListKey = 'target_roles' | 'industries' | 'regions' | 'keywords' | 'search_queries'

export function PlanCard({ plan, source, note, onChange, options, onOptions, onRun, running, disabled }: Props) {
  // List fields have server-side defaults, so the generated type marks them optional.
  const list = (k: ListKey): string[] => plan[k] ?? []
  const set = <K extends keyof Plan>(k: K, v: Plan[K]) => onChange({ ...plan, [k]: v })
  const canRun = list('search_queries').length > 0 && !disabled

  return (
    <Card data-testid="plan-card">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="text-primary size-4" aria-hidden /> Search plan
          </CardTitle>
          <Badge variant="outline" className="font-normal">
            {source === 'llm' ? 'Written by your local model' : 'Template plan'}
          </Badge>
        </div>
        <CardDescription>Edit anything before it runs. Nothing is searched until you press Run.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {note ? <Alert><AlertDescription>{note}</AlertDescription></Alert> : null}

        <div className="space-y-1.5">
          <div className="text-sm font-medium" id="plan-mode">Mode</div>
          <div role="group" aria-labelledby="plan-mode" className="flex gap-1.5">
            {MODE_OPTIONS.map((m) => (
              <Button key={m.value} size="sm" type="button" variant={plan.mode === m.value ? 'default' : 'outline'}
                aria-pressed={plan.mode === m.value} onClick={() => set('mode', m.value satisfies Mode)}>
                {m.label}
              </Button>
            ))}
          </div>
        </div>

        <div className="grid gap-5 md:grid-cols-2">
          <ChipList label="Roles to contact" items={list('target_roles')} onChange={(v) => set('target_roles', v)} placeholder="e.g. founder" />
          <ChipList label="Industries" items={list('industries')} onChange={(v) => set('industries', v)} placeholder="e.g. AI" />
          <ChipList label="Regions" items={list('regions')} onChange={(v) => set('regions', v)} placeholder="e.g. Bangalore" />
          <ChipList label="Keywords" items={list('keywords')} onChange={(v) => set('keywords', v)} placeholder="e.g. seed" />
        </div>
        <ChipList label="Search queries" items={list('search_queries')} max={12} onChange={(v) => set('search_queries', v)} placeholder="Add a search query" />

        <div className="space-y-2 border-t pt-4">
          <label className="flex items-start gap-2 text-sm">
            <Checkbox checked={options.expand_lists} onCheckedChange={(c) => onOptions({ ...options, expand_lists: c === true })} className="mt-0.5" />
            <span>
              Follow list pages
              <span className="text-muted-foreground block text-xs">Open results like “Top 50 AI startups” and crawl the companies they link to. Finds many more sites.</span>
            </span>
          </label>
          {plan.mode === 'service' ? (
            <label className="flex items-start gap-2 text-sm">
              <Checkbox checked={options.use_overpass} onCheckedChange={(c) => onOptions({ ...options, use_overpass: c === true })} className="mt-0.5" />
              <span>
                Include local businesses from OpenStreetMap
                <span className="text-muted-foreground block text-xs">Free map data; works for shops, cafes and similar places in a named city.</span>
              </span>
            </label>
          ) : null}
          <label className="flex items-start gap-2 text-sm">
            <Checkbox checked={options.recrawl} onCheckedChange={(c) => onOptions({ ...options, recrawl: c === true })} className="mt-0.5" />
            <span>
              Re-crawl sites seen in earlier searches
              <span className="text-muted-foreground block text-xs">By default sites crawled in the last 14 days are skipped.</span>
            </span>
          </label>
        </div>

        <Button onClick={onRun} disabled={!canRun || running} size="lg">
          {running ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />} Run search
        </Button>
        {disabled ? <p className="text-muted-foreground text-xs">A search is already running.</p> : null}
      </CardContent>
    </Card>
  )
}
