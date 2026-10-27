import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useJob } from '@/lib/api/hooks-campaigns'

interface Line {
  lead_id: number
  email: string
  subject: string
  decision: 'would_send' | 'blocked'
  reason: string | null
}

function asLines(result: unknown): Line[] {
  if (typeof result !== 'object' || result === null || !('lines' in result) || !Array.isArray(result.lines)) return []
  return (result.lines as unknown[]).filter((l): l is Line => typeof l === 'object' && l !== null && 'email' in l && 'decision' in l)
}

export function DryRunDialog({ jobId, onClose }: { jobId: number | null; onClose: () => void }) {
  const job = useJob(jobId)
  const j = job.data
  const lines = asLines(j?.result)
  const running = j === undefined || j.state === 'queued' || j.state === 'running'
  return (
    <Dialog open={jobId !== null} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Dry run</DialogTitle>
          <DialogDescription>Every send-safety rule is checked for each approved draft. Nothing is sent.</DialogDescription>
        </DialogHeader>
        {job.error ? (
          <div role="alert" className="text-destructive text-sm">Could not read the dry run. <button type="button" className="underline" onClick={() => void job.refetch()}>Retry</button></div>
        ) : running ? (
          <div className="flex items-center gap-2 text-sm" role="status"><Loader2 className="size-4 animate-spin" aria-hidden /> {j?.message ?? 'Starting the dry run…'}</div>
        ) : j.state !== 'succeeded' ? (
          <div role="alert" className="text-destructive text-sm">The dry run {j.state}. {j.error ?? ''}</div>
        ) : (
          <>
            <p className="text-sm" data-testid="dry-summary">
              {String((j.result as { would_send?: number } | null)?.would_send ?? 0)} would be sent, {String((j.result as { blocked?: number } | null)?.blocked ?? 0)} blocked.
            </p>
            <ul className="max-h-80 divide-y overflow-y-auto" aria-label="Dry run results">
              {lines.map((l) => (
                <li key={`${l.lead_id}-${l.email}`} className="flex items-start justify-between gap-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs">{l.email}</div>
                    <div className="text-muted-foreground truncate">{l.subject}</div>
                    {l.reason ? <div className="text-muted-foreground text-xs">{l.reason}</div> : null}
                  </div>
                  <span className={l.decision === 'would_send' ? 'text-emerald-700 dark:text-emerald-400' : 'text-destructive'}>
                    {l.decision === 'would_send' ? 'Would send' : 'Blocked'}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="flex justify-end"><Button variant="outline" onClick={onClose}>Close</Button></div>
      </DialogContent>
    </Dialog>
  )
}
