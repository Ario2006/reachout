import { Loader2, Sparkles, X } from 'lucide-react'
import { useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { ApiError } from '@/lib/api/client'
import { useCancelJob } from '@/lib/api/hooks'
import { useGenerateDrafts } from '@/lib/api/hooks-compose'
import { TERMINAL, toJobEvent, useLive, type JobEvent } from '@/lib/jobs-store'

/** "Generate drafts" with live job progress. Always shows words, never a bare spinner. */
export function GenerateControls({ campaignId, disabled }: { campaignId: number; disabled?: boolean }) {
  const gen = useGenerateDrafts()
  const cancel = useCancelJob()
  const [jobId, setJobId] = useState<number | null>(null)
  const [seed, setSeed] = useState<JobEvent | null>(null)
  const [regenerate, setRegenerate] = useState(false)
  const live = useLive((s) => (jobId === null ? undefined : s.jobs[jobId]))
  const job = live ?? seed
  const active = job !== null && !TERMINAL.has(job.state)

  async function start() {
    try {
      const j = await gen.mutateAsync({ campaignId, regenerate })
      setJobId(j.id)
      setSeed(toJobEvent(j))
    } catch { /* gen.error is shown below */ }
  }

  const pct = job && job.total > 0 ? Math.min(100, Math.round((job.progress / job.total) * 100)) : 0
  return (
    <div className="space-y-2" data-testid="generate-controls">
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => void start()} disabled={disabled || active || gen.isPending}>
          {gen.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
          {gen.isPending ? 'Starting…' : 'Generate drafts'}
        </Button>
        <div className="flex items-center gap-2">
          <Checkbox id="regen" checked={regenerate} onCheckedChange={(v) => setRegenerate(v === true)} disabled={active} />
          <Label htmlFor="regen" className="text-muted-foreground text-xs font-normal">Rewrite drafts I have not approved</Label>
        </div>
      </div>
      {gen.error ? <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-2">{gen.error instanceof ApiError ? gen.error.detail : 'Could not start.'}<Button size="xs" variant="outline" onClick={() => void start()}>Retry</Button></AlertDescription></Alert> : null}
      {job ? (
        <div className="space-y-1" aria-live="polite" data-testid="generate-progress">
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="flex min-w-0 items-center gap-2">
              {active ? <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden /> : null}
              <span className="truncate">
                {job.state === 'queued' ? 'Waiting for the worker to pick this up…'
                  : job.state === 'failed' ? `Drafting failed: ${job.error ?? 'unknown error'}`
                    : job.state === 'cancelled' ? 'Drafting cancelled. Drafts already written are kept.'
                      : (job.message ?? 'Writing drafts…')}
              </span>
            </span>
            {active ? <Button variant="ghost" size="sm" onClick={() => cancel.mutate(job.id)} disabled={cancel.isPending}><X aria-hidden /> Cancel</Button> : null}
          </div>
          {active ? <Progress value={pct} aria-label="Drafting progress" /> : null}
        </div>
      ) : null}
    </div>
  )
}
