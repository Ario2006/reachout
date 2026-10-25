import { Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { useCancelJob } from '@/lib/api/hooks'
import { TERMINAL, useLive, type JobEvent } from '@/lib/jobs-store'

/** Live progress for a model-pull job. Always shows text, never a bare spinner. */
export function PullProgress({ jobId, seed }: { jobId: number; seed?: JobEvent | null }) {
  const live = useLive((s) => s.jobs[jobId])
  const cancel = useCancelJob()
  const job = live ?? seed
  if (!job) return <p className="text-muted-foreground text-sm">Queued…</p>

  const pct = job.total > 0 ? Math.min(100, Math.round((job.progress / job.total) * 100)) : 0
  const active = !TERMINAL.has(job.state)

  return (
    <div className="space-y-2" aria-live="polite" data-testid="pull-progress">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="flex min-w-0 items-center gap-2">
          {active ? <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden /> : null}
          <span className="truncate">
            {job.state === 'queued'
              ? 'Waiting for the worker to pick this up…'
              : job.state === 'failed'
                ? 'Download failed'
                : job.state === 'cancelled'
                  ? 'Download cancelled'
                  : job.state === 'succeeded'
                    ? 'Download complete'
                    : (job.message ?? 'Downloading…')}
          </span>
        </span>
        {active ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => cancel.mutate(job.id)}
            disabled={cancel.isPending}
          >
            <X aria-hidden /> Cancel
          </Button>
        ) : null}
      </div>
      <Progress value={job.state === 'succeeded' ? 100 : pct} aria-label="Download progress" />
      {job.state === 'failed' && job.error ? (
        <p className="text-destructive text-sm" role="alert">
          {job.error}
        </p>
      ) : null}
    </div>
  )
}
