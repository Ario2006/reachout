import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useCampaignMessages, useConfirmMessage, useRetryMessage } from '@/lib/api/hooks-campaigns'
import type { MessageOut } from '@/lib/api/types'

function fail(e: unknown): void {
  toast.error(e instanceof ApiError ? e.detail : 'Something went wrong. Try again.')
}

function Row({ m }: { m: MessageOut }) {
  const confirm = useConfirmMessage()
  const retry = useRetryMessage()
  const busy = confirm.isPending || retry.isPending
  const unsure = m.state === 'needs_confirmation'
  return (
    <li className="space-y-2 py-3" data-testid={`msg-${m.id}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{m.lead_name ?? m.recipient_email}</div>
          <div className="text-muted-foreground truncate font-mono text-xs">{m.recipient_email}</div>
        </div>
        <div className="flex items-center gap-2">
          {busy ? <Loader2 className="size-4 animate-spin" aria-label="Working" /> : null}
          {unsure ? (
            <>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => confirm.mutate({ id: m.id, wasSent: true }, { onError: fail })}>Confirm sent</Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => confirm.mutate({ id: m.id, wasSent: false }, { onError: fail })}>Not sent</Button>
            </>
          ) : (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => retry.mutate(m.id, { onError: fail })}>Retry</Button>
          )}
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        {unsure
          ? 'The app stopped, or the connection dropped, while this was sending. Check Gmail > Sent for it, then say whether it went out. It is never resent on its own.'
          : (m.error ?? 'The server did not accept this email, so nothing was sent.')}
      </p>
    </li>
  )
}

/** Emails that need the user: outcome unknown (confirm) and definite failures (retry). */
export function NeedsAttention({ campaignId }: { campaignId: number }) {
  const unsure = useCampaignMessages(campaignId, 'needs_confirmation')
  const failed = useCampaignMessages(campaignId, 'failed')
  const err = unsure.error ?? failed.error
  if (err) {
    return (
      <div role="alert" className="text-destructive text-sm">
        Could not load emails that need attention: {err instanceof ApiError ? err.detail : 'unknown error'}{' '}
        <button type="button" className="underline" onClick={() => { void unsure.refetch(); void failed.refetch() }}>Retry</button>
      </div>
    )
  }
  if (unsure.isLoading || failed.isLoading) return <Skeleton className="h-16 w-full" />
  const rows = [...(unsure.data ?? []), ...(failed.data ?? [])]
  if (rows.length === 0) return <p className="text-muted-foreground text-sm">Nothing needs your attention.</p>
  return <ul className="divide-y" aria-label="Emails that need attention">{rows.map((m) => <Row key={m.id} m={m} />)}</ul>
}
