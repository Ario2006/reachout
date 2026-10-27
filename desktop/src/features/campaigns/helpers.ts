import type { Campaign, Readiness } from '@/lib/api/types'

export type CampaignState = Campaign['state']

export const STATE_LABEL: Record<CampaignState, string> = {
  draft: 'Draft',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
}

/** The cap the spec recommends for a new sending account; above it the user is warned (the backend also warns). */
export const RECOMMENDED_CAP = 25

export function capWarning(cap: number): string | null {
  if (cap > RECOMMENDED_CAP) {
    return `Above the recommended ${RECOMMENDED_CAP} a day. A new Gmail account that sends this much is likely to be throttled or flagged. The hard ceiling is 100.`
  }
  return null
}

export function formatCountdown(secondsLeft: number): string {
  const s = Math.max(0, Math.ceil(secondsLeft))
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  if (s >= 60) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${s}s`
}

export interface Progress {
  sent: number
  total: number
  percent: number
}

/** Progress of a run: sent against everything that belongs to it (queued, in flight, sent, failed, to confirm). */
export function progressOf(counts: Campaign['counts']): Progress {
  const total = counts.queued + counts.sending + counts.sent + counts.failed + counts.needs_confirmation
  return { sent: counts.sent, total, percent: total === 0 ? 0 : Math.round((counts.sent / total) * 100) }
}

/** Why Start is disabled, or null when it can be pressed. Mirrors what the backend refuses. */
export function startBlocker(campaign: Campaign, readiness: Readiness | undefined): string | null {
  if (campaign.state !== 'draft') return campaign.state === 'paused' ? 'Use Resume to continue a paused campaign.' : 'This campaign has already started.'
  if (readiness === undefined) return 'Checking readiness…'
  const blocker = readiness.items.find((i) => i.blocking && !i.ok)
  return blocker ? blocker.detail : null
}

export function testSendBlocker(campaign: Campaign, readiness: Readiness | undefined): string | null {
  if (readiness === undefined) return 'Checking readiness…'
  const gmail = readiness.items.find((i) => i.id === 'gmail')
  if (gmail && !gmail.ok) return gmail.detail
  if (campaign.counts.drafts_approved === 0) return 'Approve at least one draft in Compose first.'
  return null
}

export function dryRunBlocker(campaign: Campaign): string | null {
  return campaign.counts.drafts_approved === 0 && campaign.counts.queued === 0 ? 'Approve at least one draft in Compose first.' : null
}
