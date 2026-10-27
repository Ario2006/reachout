import { Ban, ChevronDown, Loader2, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { STATUS_OPTIONS } from '@/components/lead-badges'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ApiError } from '@/lib/api/client'
import { useBulk, type BulkBody, type BulkFilter } from '@/lib/api/hooks'
import type { LeadStatus } from '@/lib/api/types'
import { pluralize } from '@/lib/format'

interface Props {
  count: number
  total: number
  loaded: number
  ids: number[]
  allMatching: boolean
  filter: BulkFilter
  onSelectAllMatching: () => void
  onClear: () => void
}

type Confirm = 'delete' | 'suppress' | null

export function BulkBar({ count, total, loaded, ids, allMatching, filter, onSelectAllMatching, onClear }: Props) {
  const bulk = useBulk()
  const [confirm, setConfirm] = useState<Confirm>(null)
  const target = allMatching ? { filter } : { ids }
  const n = allMatching ? total : count

  async function run(body: BulkBody, done: (affected: number, skipped: number) => string) {
    try {
      const r = await bulk.mutateAsync(body)
      toast.success(done(r.affected, r.skipped))
      onClear()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'The action failed')
    }
    setConfirm(null)
  }

  return (
    <div role="region" aria-label="Bulk actions" className="bg-primary/5 border-primary/30 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-1.5 text-sm" data-testid="bulk-bar">
      <span className="font-medium">{n.toLocaleString()} selected</span>
      {!allMatching && count === loaded && total > loaded ? (
        <Button variant="link" size="xs" onClick={onSelectAllMatching}>Select all {total.toLocaleString()} matching</Button>
      ) : null}
      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant="outline" size="sm">Set status <ChevronDown aria-hidden /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {STATUS_OPTIONS.map((s) => (
              <DropdownMenuItem key={s.value} onSelect={() => void run({ action: 'set_status', status: s.value satisfies LeadStatus, ...target } as BulkBody, (a) => `${pluralize(a, 'lead')} set to ${s.label}`)}>
                {s.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild><span><Button variant="outline" size="sm" disabled>Move to campaign</Button></span></TooltipTrigger>
          <TooltipContent>Campaigns arrive in milestone 4</TooltipContent>
        </Tooltip>
        <Button variant="outline" size="sm" onClick={() => setConfirm('suppress')}><Ban aria-hidden /> Add to suppression</Button>
        <Button variant="destructive" size="sm" onClick={() => setConfirm('delete')}><Trash2 aria-hidden /> Delete</Button>
        <Button variant="ghost" size="sm" onClick={onClear}>Clear</Button>
      </div>

      <Dialog open={confirm !== null} onOpenChange={(o) => { if (!o) setConfirm(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirm === 'delete' ? `Delete ${pluralize(n, 'lead')}?` : `Never email ${pluralize(n, 'lead')}?`}</DialogTitle>
            <DialogDescription>
              {confirm === 'delete'
                ? 'They are removed from your list. Leads that already have drafts or sent emails are kept.'
                : 'Their addresses go on the suppression list and the leads are closed. You can remove manual entries later in Settings > Suppression.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={bulk.isPending}
              onClick={() => void run(
                { action: confirm === 'delete' ? 'delete' : 'suppress', ...target } as BulkBody,
                (a, s) => confirm === 'delete' ? `${pluralize(a, 'lead')} deleted${s ? `, ${s} kept (they have drafts or emails)` : ''}` : `${pluralize(a, 'address', 'addresses')} suppressed`,
              )}
            >
              {bulk.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} {confirm === 'delete' ? 'Delete' : 'Add to suppression'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
