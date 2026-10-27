import { Info } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { isFollowupsUnavailable, useFollowupPlan, useSkipFollowup } from '@/lib/api/hooks-followups'
import type { Campaign, FollowupRow, FollowupState } from '@/lib/api/types'
import { ConfirmDialog } from './confirm-dialog'
import { FollowupTable, KIND_LABEL, STATE_LABEL } from './followups-table'
import { useNowSeconds } from './campaign-status'

const ORDER: FollowupState[] = ['waiting', 'due', 'awaiting_approval', 'queued', 'sent', 'skipped']

function Counts({ counts }: { counts: Record<string, number> }) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="followup-counts">
      {ORDER.map((s) => (
        <div key={s} className="rounded-lg border px-3 py-2">
          <dt className="text-muted-foreground text-xs">{STATE_LABEL[s]}</dt>
          <dd className="text-lg font-semibold tabular-nums" data-testid={`followup-count-${s}`}>{counts[s] ?? 0}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The campaign detail's follow-up status: counts, the schedule, skip, and a way to review follow-up drafts. */
export function FollowupsSection({ campaign }: { campaign: Campaign }) {
  const plan = useFollowupPlan(campaign.id)
  const skip = useSkipFollowup(campaign.id)
  const now = useNowSeconds()
  const [target, setTarget] = useState<FollowupRow | null>(null)

  const confirmSkip = () => {
    if (!target) return
    const t = target
    skip.mutate({ leadId: t.lead_id, kind: t.kind }, {
      onSuccess: () => { toast.success('Follow-up skipped'); setTarget(null) },
      onError: (e) => { toast.error(e instanceof ApiError ? e.detail : 'Could not skip. Try again.'); setTarget(null) },
    })
  }

  const rows = plan.data?.rows ?? []
  const awaiting = plan.data?.counts['awaiting_approval'] ?? 0
  let body: React.ReactNode
  if (plan.isPending) {
    body = <div className="space-y-2" role="status" aria-label="Loading follow-ups" data-testid="followups-loading"><Skeleton className="h-16 w-full" /><Skeleton className="h-24 w-full" /></div>
  } else if (plan.isError) {
    const unavailable = isFollowupsUnavailable(plan.error)
    body = (
      <div role="alert" className="space-y-2" data-testid="followups-error">
        <p className={unavailable ? 'text-muted-foreground text-sm' : 'text-destructive text-sm'}>
          {unavailable ? 'Follow-up status is not available yet. It appears here once follow-ups are installed.' : `Could not load follow-ups: ${plan.error instanceof ApiError ? plan.error.detail : 'something went wrong'}.`}
        </p>
        <Button variant="outline" size="sm" onClick={() => void plan.refetch()}>Retry</Button>
      </div>
    )
  } else {
    body = (
      <div className="space-y-4">
        <Counts counts={plan.data.counts} />
        {awaiting > 0 ? (
          <Alert data-testid="followups-review-link"><Info aria-hidden /><AlertDescription className="flex flex-wrap items-center gap-3">
            {awaiting === 1 ? '1 follow-up is' : `${awaiting} follow-ups are`} waiting for your approval.
            <Button asChild size="sm" variant="outline"><Link to={`/compose?campaign=${campaign.id}&kind=followups`}>Review follow-up drafts</Link></Button>
          </AlertDescription></Alert>
        ) : null}
        {rows.length === 0 ? (
          <p className="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm" data-testid="followups-empty">
            {campaign.followups_enabled ? 'No follow-ups are scheduled yet. They are planned for each lead after the first email is sent.' : 'Follow-ups are off for this campaign. Turn them on in Settings above.'}
          </p>
        ) : (
          <FollowupTable rows={rows} nowS={now} onSkip={setTarget} skippingKey={skip.isPending && target ? `${target.lead_id}:${target.kind}` : null} />
        )}
      </div>
    )
  }

  return (
    <section data-slot="followups" aria-label="Follow-ups">
      <Card>
        <CardHeader><CardTitle className="text-base">Follow-ups</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground text-sm">
            Follow-ups stop automatically when a lead replies, bounces or opts out. They use the same daily cap, send window and opt-out footer as the first email, and go in the same thread.
          </p>
          {body}
        </CardContent>
      </Card>
      <ConfirmDialog
        open={target !== null}
        title="Skip this follow-up?"
        confirmLabel="Skip follow-up"
        busy={skip.isPending}
        onCancel={() => setTarget(null)}
        onConfirm={confirmSkip}
      >
        {target ? <p>{KIND_LABEL[target.kind]} for {target.name ?? target.email} will not be sent. This cannot be undone.</p> : null}
      </ConfirmDialog>
    </section>
  )
}
