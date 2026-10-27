import { Lock, Loader2, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ApiError } from '@/lib/api/client'
import { useAddSuppression, useRemoveSuppression, useSuppression } from '@/lib/api/hooks'
import type { SuppressionEntry } from '@/lib/api/types'
import { Section } from '../form-bits'

export function SuppressionTab() {
  const [q, setQ] = useState('')
  const [email, setEmail] = useState('')
  const [target, setTarget] = useState<SuppressionEntry | null>(null)
  const list = useSuppression(q.trim())
  const add = useAddSuppression()

  function onAdd(ev: React.FormEvent) {
    ev.preventDefault()
    add.mutate(email.trim(), {
      onSuccess: () => { setEmail(''); toast.success('Added to the suppression list') },
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : 'Could not add'),
    })
  }

  return (
    <Section
      title="Suppression list"
      description="Addresses here are never emailed, including follow-ups. Bounces and opt-outs are added automatically and cannot be removed."
    >
      <div className="flex flex-wrap gap-2">
        <Input aria-label="Search suppression list" className="max-w-64" placeholder="Search…" value={q} onChange={(ev) => setQ(ev.target.value)} />
        <form onSubmit={onAdd} className="flex gap-2">
          <Input aria-label="Email to suppress" type="email" className="w-64" placeholder="Add an address to never email" value={email} onChange={(ev) => setEmail(ev.target.value)} />
          <Button type="submit" variant="secondary" disabled={add.isPending || email.trim() === ''}>
            {add.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Add
          </Button>
        </form>
      </div>

      {list.isPending ? (
        <div className="space-y-2" role="status" aria-label="Loading suppression list"><Skeleton className="h-9" /><Skeleton className="h-9" /></div>
      ) : list.isError ? (
        <div className="text-destructive flex items-center gap-3 text-sm" role="alert">
          Could not load the list.
          <Button size="sm" variant="outline" onClick={() => void list.refetch()}>Retry</Button>
        </div>
      ) : list.data.length === 0 ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
          {q ? 'No matches.' : 'Nobody is suppressed yet. Bounces and opt-outs will appear here.'}
        </p>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow><TableHead>Email</TableHead><TableHead>Reason</TableHead><TableHead>Added</TableHead><TableHead className="w-12"><span className="sr-only">Actions</span></TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((row) => (
                <TableRow key={row.email}>
                  <TableCell className="font-mono text-xs">{row.email}</TableCell>
                  <TableCell><Badge variant={row.removable ? 'secondary' : 'outline'}>{row.reason ?? 'unknown'}</Badge></TableCell>
                  <TableCell className="text-muted-foreground text-xs">{row.added_at}</TableCell>
                  <TableCell>
                    {row.removable ? (
                      <Button size="icon-sm" variant="ghost" aria-label={`Remove ${row.email}`} onClick={() => setTarget(row)}>
                        <Trash2 aria-hidden />
                      </Button>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-muted-foreground inline-flex size-7 items-center justify-center" aria-label="Protected: added automatically">
                            <Lock className="size-4" aria-hidden />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>Added automatically, so it cannot be removed</TooltipContent>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <RemoveDialog entry={target} onClose={() => setTarget(null)} />
    </Section>
  )
}

function RemoveDialog({ entry, onClose }: { entry: SuppressionEntry | null; onClose: () => void }) {
  const [typed, setTyped] = useState('')
  const remove = useRemoveSuppression()
  const matches = entry !== null && typed.trim().toLowerCase() === entry.email

  function close() { setTyped(''); onClose() }

  return (
    <Dialog open={entry !== null} onOpenChange={(open) => { if (!open) close() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove from suppression list?</DialogTitle>
          <DialogDescription>
            Once removed, this address can receive emails again. Type{' '}
            <span className="text-foreground font-mono">{entry?.email}</span> to confirm.
          </DialogDescription>
        </DialogHeader>
        <Input aria-label="Type the email to confirm" autoComplete="off" value={typed} onChange={(ev) => setTyped(ev.target.value)} />
        <DialogFooter>
          <Button variant="ghost" onClick={close}>Cancel</Button>
          <Button
            variant="destructive"
            disabled={!matches || remove.isPending}
            onClick={() => {
              if (!entry) return
              remove.mutate({ email: entry.email, confirm: typed.trim() }, {
                onSuccess: () => { toast.success('Removed'); close() },
                onError: (err) => toast.error(err instanceof ApiError ? err.detail : 'Could not remove'),
              })
            }}
          >
            {remove.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Remove
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
