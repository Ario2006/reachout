import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { DraftFlag, DraftStatus } from '@/lib/api/types'
import { cn } from '@/lib/utils'

const LABEL: Record<DraftFlag['code'], string> = {
  needs_review: 'Needs review',
  opener_fallback: 'Neutral opener',
  banned_phrase: 'Banned phrase',
  too_long: 'Too long',
  unsupported_fact: 'Unsupported claim',
  missing_field: 'Missing field',
  guessed_email: 'Guessed address',
  no_context: 'No source text',
  edited: 'Edited',
}
const WARN = 'border-amber-600/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'

export function FlagBadges({ flags, max }: { flags: DraftFlag[]; max?: number }) {
  const shown = max === undefined ? flags : flags.slice(0, max)
  return (
    <>
      {shown.map((f) => (
        <Tooltip key={f.code}>
          <TooltipTrigger asChild>
            <Badge variant="outline" className={cn('h-5 px-1.5 text-[11px] font-normal', f.code === 'edited' ? '' : WARN)} data-testid={`flag-${f.code}`}>
              {LABEL[f.code]}
            </Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{f.message}</TooltipContent>
        </Tooltip>
      ))}
      {max !== undefined && flags.length > max ? <span className="text-muted-foreground text-[11px]">+{flags.length - max}</span> : null}
    </>
  )
}

const STATUS: Record<DraftStatus, { label: string; cls: string }> = {
  pending: { label: 'To review', cls: '' },
  needs_review: { label: 'Needs review', cls: WARN },
  approved: { label: 'Approved', cls: 'border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300' },
  rejected: { label: 'Rejected', cls: 'border-border bg-muted text-muted-foreground' },
}

export function DraftStatusBadge({ status }: { status: DraftStatus }) {
  const s = STATUS[status]
  return <Badge variant="outline" className={cn('h-5 px-1.5 text-[11px] font-normal', s.cls)} data-testid={`status-${status}`}>{s.label}</Badge>
}
