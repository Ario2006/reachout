import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Ban, BellOff, ExternalLink, MailX, MessageSquare, Send } from 'lucide-react'
import { useEffect } from 'react'
import { toast } from 'sonner'
import { ConfidenceBadge, StatusBadge } from '@/components/lead-badges'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useConfirmOptOut, useSetLeadStatus, useThread, type StatusChange } from '@/lib/api/hooks-inbox'
import { K } from '@/lib/api/keys'
import type { ThreadDetail, ThreadEntry } from '@/lib/api/types'
import { relativeTime, safeHttpUrl } from '@/lib/format'
import { cn } from '@/lib/utils'

const OUTCOME_LABEL = { interested: 'Interested', not_interested: 'Not interested', meeting_booked: 'Meeting booked' } as const
const SEND_LABEL = { initial: 'You wrote', followup_1: 'You followed up', followup_2: 'You followed up again' } as const

/** The quick buttons. "Not interested" also closes the lead so no follow-up goes out. */
export const QUICK_ACTIONS: { label: string; change: StatusChange }[] = [
  { label: 'Interested', change: { outcome: 'interested' } },
  { label: 'Not interested', change: { outcome: 'not_interested', status: 'closed' } },
  { label: 'Meeting booked', change: { outcome: 'meeting_booked' } },
  { label: 'Close', change: { status: 'closed' } },
]

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'Something went wrong. Try again.'
}

export function ThreadView({ leadId }: { leadId: number }) {
  const qc = useQueryClient()
  const thread = useThread(leadId)
  const unread = thread.data?.entries.some((e) => e.unread) ?? false
  // Opening a thread marks its replies read on the server; refresh the list (counts) but keep this view's highlights.
  useEffect(() => {
    if (unread) void qc.invalidateQueries({ queryKey: [...K.threads, 'list'] })
  }, [unread, qc, leadId])

  if (thread.isPending) {
    return <div className="space-y-3 p-6" aria-label="Loading conversation"><Skeleton className="h-10 w-1/2" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
  }
  if (thread.isError) {
    return (
      <div className="p-6" role="alert">
        <p className="text-destructive text-sm">Could not load this conversation. {errorText(thread.error)}</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void thread.refetch()}>Retry</Button>
      </div>
    )
  }
  return <ThreadBody key={leadId} thread={thread.data} />
}

function ThreadBody({ thread }: { thread: ThreadDetail }) {
  const setStatus = useSetLeadStatus()
  const { lead } = thread
  const gmail = safeHttpUrl(thread.gmail_url)

  function apply(change: StatusChange, done: string) {
    setStatus.mutate({ leadId: lead.id, change }, { onSuccess: () => toast.success(done), onError: (e) => toast.error(errorText(e)) })
  }

  return (
    <div className="flex h-full flex-col" data-testid="thread-view">
      <header className="space-y-3 border-b p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{lead.name || lead.email}</h2>
            <p className="text-muted-foreground truncate text-sm">
              <span className="font-mono text-xs">{lead.email}</span>
              {lead.company ? ` · ${lead.company}` : ''}{lead.role ? ` · ${lead.role}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <ConfidenceBadge value={lead.email_confidence} />
            <StatusBadge value={lead.status} />
            {lead.outcome && <Badge variant="secondary" data-testid="outcome">{OUTCOME_LABEL[lead.outcome]}</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {QUICK_ACTIONS.map(({ label, change }) => (
            <Button key={label} size="sm" variant={change.outcome && lead.outcome === change.outcome ? 'default' : 'outline'}
              disabled={setStatus.isPending} onClick={() => apply(change, `${label}: saved`)}>
              {label}
            </Button>
          ))}
          {gmail && (
            <Button asChild size="sm" variant="ghost" className="ml-auto">
              <a href={gmail.href} target="_blank" rel="noreferrer noopener">Open in Gmail <ExternalLink aria-hidden /></a>
            </Button>
          )}
        </div>
        {setStatus.isError && (
          <p className="text-destructive text-sm" role="alert">{errorText(setStatus.error)}</p>
        )}
      </header>
      <ol className="flex-1 space-y-3 overflow-y-auto p-4" aria-label="Conversation">
        {thread.entries.length === 0 && <li className="text-muted-foreground text-sm">Nothing has been sent to this lead yet.</li>}
        {thread.entries.map((entry, i) => <Entry key={`${entry.kind}-${entry.event_id ?? entry.message_id ?? i}`} entry={entry} />)}
      </ol>
    </div>
  )
}

function Entry({ entry }: { entry: ThreadEntry }) {
  const confirm = useConfirmOptOut()
  const when = relativeTime(entry.at)
  const sent = entry.kind === 'sent'
  const style: Record<ThreadEntry['kind'], string> = {
    sent: 'bg-muted/40 ml-8',
    reply: 'bg-card mr-8 border-primary/40',
    auto_reply: 'mr-8 border-dashed bg-transparent text-muted-foreground',
    bounce: 'mr-8 border-destructive/40 bg-destructive/5',
    opt_out: 'mr-8 border-amber-500/60 bg-amber-500/10',
  }
  const title: Record<ThreadEntry['kind'], { icon: typeof Send; text: string }> = {
    sent: { icon: Send, text: entry.send_kind ? SEND_LABEL[entry.send_kind] : 'You wrote' },
    reply: { icon: MessageSquare, text: 'They replied' },
    auto_reply: { icon: BellOff, text: 'Automatic reply (not a real answer)' },
    bounce: { icon: MailX, text: entry.status_code ? `Delivery failed (${entry.status_code})` : 'Delivery failed' },
    opt_out: { icon: Ban, text: 'Asked to stop' },
  }
  const T = title[entry.kind]
  return (
    <li className={cn('rounded-lg border p-3 text-sm', style[entry.kind])} data-testid={`entry-${entry.kind}`}>
      <div className="flex items-center gap-2 text-xs font-medium">
        <T.icon className="size-3.5" aria-hidden />
        <span>{T.text}</span>
        {entry.unread && <Badge className="h-4 px-1 text-[10px]">New</Badge>}
        {entry.kind === 'bounce' && entry.status_code?.startsWith('4') && <span className="text-muted-foreground font-normal">temporary, address kept</span>}
        <span className="text-muted-foreground ml-auto font-normal">{when}</span>
      </div>
      {entry.subject && <p className="mt-1 font-medium">{entry.subject}</p>}
      {entry.text && <p className={cn('mt-1 whitespace-pre-wrap', sent && 'text-muted-foreground')}>{entry.text}</p>}
      {entry.kind === 'opt_out' && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <AlertTriangle className="size-3.5 text-amber-600" aria-hidden />
          <span className="text-xs">This address is already suppressed. Check that the reply really asks to stop.</span>
          {entry.needs_confirmation && entry.event_id !== null && entry.event_id !== undefined ? (
            <Button size="sm" variant="outline" disabled={confirm.isPending}
              onClick={() => confirm.mutate(entry.event_id as number, { onSuccess: () => toast.success('Opt-out confirmed'), onError: (e) => toast.error(errorText(e)) })}>
              Confirm opt-out
            </Button>
          ) : <Badge variant="secondary">Confirmed</Badge>}
        </div>
      )}
    </li>
  )
}
