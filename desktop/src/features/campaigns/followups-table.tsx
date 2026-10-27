import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { FollowupKind, FollowupRow, FollowupState } from '@/lib/api/types'

export const STATE_LABEL: Record<FollowupState, string> = {
  waiting: 'Waiting', due: 'Due', awaiting_approval: 'Awaiting approval', queued: 'Queued', sent: 'Sent', skipped: 'Skipped',
}
const STATE_VARIANT: Record<FollowupState, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  waiting: 'outline', due: 'default', awaiting_approval: 'secondary', queued: 'secondary', sent: 'outline', skipped: 'outline',
}
export const KIND_LABEL: Record<FollowupKind, string> = { followup_1: 'Follow-up 1', followup_2: 'Follow-up 2' }

/** Rows in these states can still be skipped. */
export const SKIPPABLE: ReadonlySet<FollowupState> = new Set(['waiting', 'due', 'awaiting_approval', 'queued'])

/** "in 3 days", "2 hours ago", "in under a minute". `nowS` and `dueS` are epoch seconds. */
export function relativeDue(dueS: number, nowS: number): string {
  const diff = dueS - nowS
  const abs = Math.abs(diff)
  if (abs < 60) return diff >= 0 ? 'in under a minute' : 'just now'
  const unit = abs < 3600 ? [Math.round(abs / 60), 'minute'] as const : abs < 86400 ? [Math.round(abs / 3600), 'hour'] as const : [Math.round(abs / 86400), 'day'] as const
  const label = `${unit[0]} ${unit[1]}${unit[0] === 1 ? '' : 's'}`
  return diff >= 0 ? `in ${label}` : `${label} ago`
}

export function absoluteDue(dueS: number): string {
  return new Date(dueS * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function FollowupTable({ rows, nowS, onSkip, skippingKey }: {
  rows: FollowupRow[]
  nowS: number
  onSkip: (r: FollowupRow) => void
  skippingKey: string | null
}) {
  return (
    <Table aria-label="Follow-up schedule" data-testid="followup-table">
      <TableHeader>
        <TableRow>
          <TableHead>Lead</TableHead><TableHead>Follow-up</TableHead><TableHead>Due</TableHead><TableHead>State</TableHead><TableHead>Note</TableHead>
          <TableHead className="text-right"><span className="sr-only">Actions</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => {
          const key = `${r.lead_id}:${r.kind}`
          const who = r.name ?? r.email
          return (
            <TableRow key={key} data-testid={`followup-row-${key}`}>
              <TableCell className="max-w-48">
                <div className="truncate font-medium">{who}</div>
                {r.name ? <div className="text-muted-foreground truncate font-mono text-xs">{r.email}</div> : null}
              </TableCell>
              <TableCell>{KIND_LABEL[r.kind]}</TableCell>
              <TableCell>
                <div>{relativeDue(r.due_at, nowS)}</div>
                <div className="text-muted-foreground text-xs">{absoluteDue(r.due_at)}</div>
              </TableCell>
              <TableCell><Badge variant={STATE_VARIANT[r.state]}>{STATE_LABEL[r.state]}</Badge></TableCell>
              <TableCell className="text-muted-foreground max-w-56 whitespace-normal text-xs">{r.reason ?? ''}</TableCell>
              <TableCell className="text-right">
                {SKIPPABLE.has(r.state) ? (
                  <Button size="sm" variant="outline" disabled={skippingKey === key} aria-label={`Skip ${KIND_LABEL[r.kind].toLowerCase()} for ${who}`} onClick={() => onSkip(r)}>Skip</Button>
                ) : null}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
