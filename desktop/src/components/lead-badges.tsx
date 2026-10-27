import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { EmailConfidence, LeadStatus } from '@/lib/api/types'
import { cn } from '@/lib/utils'

const CONF: Record<EmailConfidence, { label: string; cls: string; help: string }> = {
  published_personal: {
    label: 'Personal',
    cls: 'border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300',
    help: "A person's address published on their own site.",
  },
  published_generic: {
    label: 'Generic',
    cls: 'border-border bg-muted text-muted-foreground',
    help: 'A role address such as info@ or careers@, published on the site.',
  },
  guessed: {
    label: 'Guessed',
    cls: 'border-amber-600/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
    help: 'Built from a name and the domain pattern. Only the domain was checked, so it may bounce. Excluded from sending by default.',
  },
}

/** 0-100 lead score, colour-banded. The drawer lists the reasons behind it. */
export function ScoreBadge({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground text-xs" title="Imported leads are not scored">–</span>
  const cls = value >= 75 ? 'border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300'
    : value >= 50 ? 'border-border bg-muted text-foreground' : 'border-amber-600/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  return <Badge variant="outline" className={cn('h-5 min-w-9 justify-center px-1.5 text-[11px] font-medium tabular-nums', cls)} data-testid="score" aria-label={`Score ${value} out of 100`}>{value}</Badge>
}

export function ConfidenceBadge({ value }: { value: EmailConfidence }) {
  const c = CONF[value]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className={cn('h-5 px-1.5 text-[11px] font-normal', c.cls)} data-testid={`conf-${value}`}>
          {c.label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="top">{c.help}</TooltipContent>
    </Tooltip>
  )
}

export const STATUS_LABEL: Record<LeadStatus, string> = {
  new: 'New', drafted: 'Drafted', approved: 'Approved', queued: 'Queued', sent: 'Sent', replied: 'Replied',
  bounced: 'Bounced', opted_out: 'Opted out', auto_reply: 'Auto-reply', closed: 'Closed',
}

const STATUS_CLS: Partial<Record<LeadStatus, string>> = {
  replied: 'border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300',
  bounced: 'border-destructive/30 bg-destructive/10 text-destructive',
  opted_out: 'border-destructive/30 bg-destructive/10 text-destructive',
  sent: 'border-primary/30 bg-primary/10 text-primary',
}

export function StatusBadge({ value }: { value: LeadStatus }) {
  return (
    <Badge variant="outline" className={cn('h-5 px-1.5 text-[11px] font-normal', STATUS_CLS[value] ?? 'text-muted-foreground')}>
      {STATUS_LABEL[value]}
    </Badge>
  )
}

export const CONFIDENCE_OPTIONS = (Object.keys(CONF) as EmailConfidence[]).map((v) => ({ value: v, label: CONF[v].label }))
export const STATUS_OPTIONS = (Object.keys(STATUS_LABEL) as LeadStatus[]).map((v) => ({ value: v, label: STATUS_LABEL[v] }))
export const MODE_OPTIONS = [
  { value: 'internship' as const, label: 'Internship' },
  { value: 'investor' as const, label: 'Investor' },
  { value: 'service' as const, label: 'Service' },
]
