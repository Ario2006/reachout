import { AlertTriangle, Coffee, PauseCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import type { Campaign } from '@/lib/api/types'
import { pluralize } from '@/lib/format'
import { formatCountdown, progressOf, STATE_LABEL, type CampaignState } from './helpers'

const BADGE: Record<CampaignState, 'default' | 'secondary' | 'outline'> = {
  running: 'default',
  paused: 'secondary',
  draft: 'outline',
  done: 'outline',
}

export function StateBadge({ state }: { state: CampaignState }) {
  return <Badge variant={BADGE[state]} data-testid={`state-${state}`}>{STATE_LABEL[state]}</Badge>
}

/** Seconds since epoch, ticking once a second while mounted. */
export function useNowSeconds(): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

export function RunProgress({ campaign, caffeinate }: { campaign: Campaign; caffeinate: boolean }) {
  const now = useNowSeconds()
  const p = progressOf(campaign.counts)
  const left = campaign.next_send_at !== null ? campaign.next_send_at - now : null
  let next: string | null = null
  if (campaign.state === 'running') {
    if (campaign.counts.sending > 0) next = 'Sending an email now…'
    else if (left !== null && left > 0) next = `Next email in ${formatCountdown(left)}`
    else if (p.total > p.sent) next = 'Next email: waiting for the send window or a free slot'
  }
  return (
    <section aria-label="Progress" className="space-y-2">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium">{p.total === 0 ? 'Nothing queued yet' : `${p.sent} of ${pluralize(p.total, 'email')} sent`}</span>
        <span className="text-muted-foreground font-mono text-xs" data-testid="next-send">{next ?? ''}</span>
      </div>
      <Progress value={p.percent} aria-label={`${p.percent}% sent`} />
      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>{campaign.counts.queued} queued</span>
        <span>{campaign.counts.failed} failed</span>
        <span>{campaign.counts.needs_confirmation} to confirm</span>
        {caffeinate ? (
          <span className="flex items-center gap-1" data-testid="caffeinate">
            <Coffee className="size-3" aria-hidden /> Keeping this Mac awake while sending
          </span>
        ) : null}
      </div>
    </section>
  )
}

const PAUSE_TITLE: Record<string, string> = {
  bounce_rate: 'Paused automatically: too many bounces',
  smtp_login: 'Paused automatically: cannot send through Gmail',
  shutdown: 'Paused because the app was closed',
  all: 'Paused: all sending is switched off',
  user: 'Paused',
}

export function PauseBanner({ campaign }: { campaign: Campaign }) {
  if (campaign.state !== 'paused' || !campaign.paused_by) return null
  const automatic = campaign.paused_by === 'bounce_rate' || campaign.paused_by === 'smtp_login'
  const Icon = automatic ? AlertTriangle : PauseCircle
  return (
    <Alert variant={automatic ? 'destructive' : 'default'} data-testid="pause-banner">
      <Icon aria-hidden />
      <AlertTitle>{PAUSE_TITLE[campaign.paused_by] ?? 'Paused'}</AlertTitle>
      <AlertDescription>{campaign.pause_reason ?? 'Press Resume to continue.'}</AlertDescription>
    </Alert>
  )
}
