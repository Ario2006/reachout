import { Inbox as InboxIcon, Loader2, RefreshCw, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { INBOX_FILTERS, useCheckNow, useInboxStatus, useThreads, type InboxFilter } from '@/lib/api/hooks-inbox'
import type { ThreadSummary } from '@/lib/api/types'
import { pluralize, relativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ThreadView } from './thread-view'

const KIND_LABEL: Record<NonNullable<ThreadSummary['last_kind']>, string> = {
  sent: 'You', reply: 'Reply', bounce: 'Bounced', auto_reply: 'Auto-reply', opt_out: 'Asked to stop',
}

function isFilter(v: string | null): v is InboxFilter {
  return INBOX_FILTERS.some((f) => f.value === v)
}

/** "Last checked 3m ago", or the reason the check failed. */
function CheckStatus() {
  const status = useInboxStatus()
  const check = useCheckNow()
  const checking = check.isPending || status.data?.checking === true
  const last = status.data?.last_checked_at
  const lastIso = last ? new Date(last * 1000).toISOString() : null
  return (
    <div className="flex items-center gap-3">
      <div className="text-right text-xs" aria-live="polite">
        {checking ? <span className="text-muted-foreground">Checking Gmail...</span>
          : status.isError ? <span className="text-destructive">Could not read the check status</span>
            : <span className="text-muted-foreground">{lastIso ? `Last checked ${relativeTime(lastIso)}` : 'Not checked yet'}</span>}
        {status.data?.last_error && !checking && (
          <p className="text-destructive max-w-72" role="alert" data-testid="inbox-error">{status.data.last_error}</p>
        )}
      </div>
      <Button size="sm" variant="outline" disabled={checking}
        onClick={() => check.mutate(undefined, { onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not start the check') })}>
        {checking ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />} Check now
      </Button>
    </div>
  )
}

function ThreadRow({ t, selected, onSelect }: { t: ThreadSummary; selected: boolean; onSelect: () => void }) {
  return (
    <li>
      <button type="button" onClick={onSelect} aria-current={selected ? 'true' : undefined} data-testid="thread-row"
        className={cn('hover:bg-muted/60 focus-visible:ring-ring w-full border-b px-3 py-2.5 text-left outline-none focus-visible:ring-2', selected && 'bg-muted')}>
        <div className="flex items-center gap-2">
          {t.unread > 0 && <span className="bg-primary size-2 shrink-0 rounded-full" aria-label={`${t.unread} unread`} />}
          <span className={cn('truncate text-sm', t.unread > 0 ? 'font-semibold' : 'font-medium')}>{t.lead.name || t.lead.email}</span>
          <span className="text-muted-foreground ml-auto shrink-0 text-xs">{relativeTime(t.last_activity_at)}</span>
        </div>
        <div className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-xs">
          {t.lead.company && <span className="truncate">{t.lead.company}</span>}
          {t.opt_out_to_confirm && <Badge className="h-4 border-amber-500/60 bg-amber-500/15 px-1 text-[10px] text-amber-700 dark:text-amber-300" variant="outline">Check opt-out</Badge>}
        </div>
        {t.snippet && (
          <p className="text-muted-foreground mt-0.5 truncate text-xs">
            {t.last_kind && <span className="text-foreground/70">{KIND_LABEL[t.last_kind]}: </span>}{t.snippet}
          </p>
        )}
      </button>
    </li>
  )
}

export function InboxPage() {
  const [sp, setSp] = useSearchParams()
  const rawFilter = sp.get('filter')
  const filter: InboxFilter = isFilter(rawFilter) ? rawFilter : 'all'
  const selectedParam = Number(sp.get('lead'))
  const selected = Number.isInteger(selectedParam) && selectedParam > 0 ? selectedParam : null

  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  const threads = useThreads(filter, q)
  const items = threads.data?.items ?? []
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) { if (v === null) next.delete(k); else next.set(k, v) }
    setSp(next, { replace: true })
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h1 className="text-xl font-semibold">Inbox</h1>
          <p className="text-muted-foreground text-xs">
            {threads.data ? `${pluralize(threads.data.total, 'conversation')}${threads.data.unread_total > 0 ? `, ${threads.data.unread_total} unread` : ''}` : 'Replies, bounces and auto-replies, grouped by lead'}
          </p>
        </div>
        <CheckStatus />
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,360px)_1fr]">
        <section className="flex min-h-0 flex-col border-r" aria-label="Conversations">
          <div className="space-y-2 border-b p-3">
            <div className="relative">
              <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" aria-hidden />
              <Input aria-label="Search conversations" placeholder="Search name, company or text" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter conversations">
              {INBOX_FILTERS.map((f) => (
                <Button key={f.value} size="xs" variant={filter === f.value ? 'default' : 'outline'} aria-pressed={filter === f.value}
                  onClick={() => set({ filter: f.value === 'all' ? null : f.value })}>{f.label}</Button>
              ))}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {threads.isPending ? (
              <div className="space-y-2 p-3" aria-label="Loading conversations">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14" />)}</div>
            ) : threads.isError ? (
              <div className="p-4" role="alert">
                <p className="text-destructive text-sm">Could not load conversations. {threads.error instanceof ApiError ? threads.error.detail : ''}</p>
                <Button size="sm" variant="outline" className="mt-2" onClick={() => void threads.refetch()}>Retry</Button>
              </div>
            ) : items.length === 0 ? (
              <div className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-sm" data-testid="inbox-empty">
                <InboxIcon className="size-6" aria-hidden />
                {filter === 'all' && !q ? <p>No conversations yet. Replies and bounces show up here once emails go out and Gmail is checked.</p> : <p>Nothing matches this filter.</p>}
              </div>
            ) : (
              <ul>{items.map((t) => <ThreadRow key={t.lead.id} t={t} selected={t.lead.id === selected} onSelect={() => set({ lead: String(t.lead.id) })} />)}</ul>
            )}
          </div>
        </section>
        <section className="min-h-0" aria-label="Conversation">
          {selected !== null ? <ThreadView leadId={selected} /> : (
            <div className="text-muted-foreground flex h-full items-center justify-center p-8 text-sm">Select a conversation to read it.</div>
          )}
        </section>
      </div>
    </div>
  )
}
