import { FlaskConical, Mail, Pause, Play, RotateCw } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { pluralize } from '@/lib/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ApiError } from '@/lib/api/client'
import { useCampaign, useCampaignAction, useDryRun, useReadiness, useSendingStatus, useTestSend } from '@/lib/api/hooks-campaigns'
import { ConfirmDialog } from './confirm-dialog'
import { PauseBanner, RunProgress, StateBadge } from './campaign-status'
import { DryRunDialog } from './dry-run-dialog'
import { dryRunBlocker, startBlocker, testSendBlocker } from './helpers'
import { NeedsAttention } from './needs-confirmation'
import { ReadinessList } from './readiness-card'
import { SettingsForm } from './settings-form'

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'Something went wrong. Try again.'
}

/** A button that says why it is disabled (tooltip and accessible description). */
function Guarded({ reason, children }: { reason: string | null; children: React.ReactNode }) {
  if (reason === null) return <>{children}</>
  return (
    <Tooltip>
      <TooltipTrigger asChild><span tabIndex={0} className="inline-flex" aria-label={`Unavailable: ${reason}`}>{children}</span></TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  )
}

export function CampaignDetail({ id }: { id: number }) {
  const campaign = useCampaign(id)
  const readiness = useReadiness(id)
  const sending = useSendingStatus()
  const act = useCampaignAction(id)
  const testSend = useTestSend(id)
  const dry = useDryRun(id)
  const [jobId, setJobId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [askStart, setAskStart] = useState(false)

  if (campaign.isLoading) {
    return <div className="space-y-4 p-6" aria-label="Loading campaign"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-24 w-full" /><Skeleton className="h-48 w-full" /></div>
  }
  if (campaign.error || !campaign.data) {
    return (
      <div role="alert" className="space-y-2 p-6">
        <p className="text-destructive text-sm">Could not load this campaign: {campaign.error ? errText(campaign.error) : 'not found'}.</p>
        <Button variant="outline" onClick={() => void campaign.refetch()}>Retry</Button>
      </div>
    )
  }
  const c = campaign.data
  const startReason = startBlocker(c, readiness.data)
  const testReason = testSendBlocker(c, readiness.data)
  const dryReason = dryRunBlocker(c)

  const run = (action: 'start' | 'pause' | 'resume') => {
    setActionError(null)
    act.mutate(action, { onError: (e) => setActionError(errText(e)) })
  }

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <h2 className="truncate text-lg font-semibold">{c.name}</h2>
          <StateBadge state={c.state} />
        </div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Campaign actions">
          <Guarded reason={testReason}>
            <Button variant="outline" disabled={testReason !== null || testSend.isPending}
              onClick={() => testSend.mutate(undefined, { onSuccess: (r) => toast.success(`Test sent to ${r.to}`), onError: (e) => toast.error(errText(e)) })}>
              <Mail aria-hidden /> Send test to myself
            </Button>
          </Guarded>
          <Guarded reason={dryReason}>
            <Button variant="outline" disabled={dryReason !== null || dry.isPending}
              onClick={() => dry.mutate(undefined, { onSuccess: (j) => setJobId(j.id), onError: (e) => toast.error(errText(e)) })}>
              <FlaskConical aria-hidden /> Dry run
            </Button>
          </Guarded>
          {c.state === 'draft' ? (
            <Guarded reason={startReason}>
              <Button disabled={startReason !== null || act.isPending} onClick={() => setAskStart(true)}><Play aria-hidden /> Start</Button>
            </Guarded>
          ) : null}
          {c.state === 'running' ? <Button variant="secondary" disabled={act.isPending} onClick={() => run('pause')}><Pause aria-hidden /> Pause</Button> : null}
          {c.state === 'paused' ? <Button disabled={act.isPending} onClick={() => run('resume')}><RotateCw aria-hidden /> Resume</Button> : null}
        </div>
      </header>

      {startReason !== null && c.state === 'draft' ? <p className="text-muted-foreground -mt-3 text-sm" data-testid="start-reason">Start is unavailable: {startReason}</p> : null}
      {actionError ? <p role="alert" className="text-destructive text-sm" data-testid="action-error">{actionError}</p> : null}
      <PauseBanner campaign={c} />
      {c.state !== 'draft' ? <RunProgress campaign={c} caffeinate={sending.data?.caffeinate_active ?? false} /> : null}

      <Card>
        <CardHeader><CardTitle className="text-base">Readiness</CardTitle></CardHeader>
        <CardContent>
          <ReadinessList readiness={readiness.data} loading={readiness.isLoading} error={readiness.error ? errText(readiness.error) : null} onRetry={() => void readiness.refetch()} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Needs your attention</CardTitle></CardHeader>
        <CardContent><NeedsAttention campaignId={id} /></CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Settings</CardTitle></CardHeader>
        <CardContent><SettingsForm campaign={c} /></CardContent>
      </Card>

      {/* FOLLOW-UPS SLOT: owned by the follow-ups UI task (desktop/src/features/campaigns/followups-*.tsx). It mounts here with one line. */}
      <section data-slot="followups" aria-label="Follow-ups" className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
        Follow-up schedule: coming in a later update.
      </section>

      <ConfirmDialog
        open={askStart}
        title="Start sending?"
        confirmLabel="Start sending"
        busy={act.isPending}
        onCancel={() => setAskStart(false)}
        onConfirm={() => { setAskStart(false); run('start') }}
      >
        <p data-testid="start-summary">
          {pluralize(c.counts.drafts_approved, 'approved email')} will be queued: at most {c.daily_cap} a day, between {c.window_start} and {c.window_end},
          {' '}{c.delay_min_s ?? 60} to {c.delay_max_s ?? 180} seconds apart (your Settings apply where this campaign has no override).
        </p>
        <p className="mt-2">Every email carries your opt-out footer. You can pause at any time.</p>
      </ConfirmDialog>

      <DryRunDialog jobId={jobId} onClose={() => setJobId(null)} />
    </div>
  )
}
