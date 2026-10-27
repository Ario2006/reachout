import { Copy, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ConfidenceBadge, ScoreBadge, STATUS_OPTIONS } from '@/components/lead-badges'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useLead, usePatchLead } from '@/lib/api/hooks'
import type { LeadDetail, LeadStatus } from '@/lib/api/types'
import { relativeTime, safeHttpUrl } from '@/lib/format'

const EVENT_LABEL: Record<string, string> = { sent: 'Email sent', reply: 'Replied', bounce: 'Bounced', auto_reply: 'Auto-reply', opt_out: 'Asked to stop' }

export function LeadDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const lead = useLead(id)
  return (
    <Sheet open={id !== null} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md" aria-describedby="lead-drawer-desc">
        <SheetHeader>
          <SheetTitle>{lead.data?.name || lead.data?.email || 'Lead'}</SheetTitle>
          <SheetDescription id="lead-drawer-desc">Details, source and activity for this lead.</SheetDescription>
        </SheetHeader>
        {lead.isPending ? <div className="space-y-3 p-4"><Skeleton className="h-8" /><Skeleton className="h-24" /></div>
          : lead.isError ? (
            <div className="space-y-2 p-4" role="alert">
              <p className="text-destructive text-sm">Could not load this lead. {lead.error instanceof ApiError ? lead.error.detail : ''}</p>
              <Button size="sm" variant="outline" onClick={() => void lead.refetch()}>Retry</Button>
            </div>
          )
            : <DrawerBody key={`${lead.data.id}-${lead.data.updated_at}`} lead={lead.data} />}
      </SheetContent>
    </Sheet>
  )
}

function DrawerBody({ lead }: { lead: LeadDetail }) {
  const patch = usePatchLead()
  const [form, setForm] = useState({ name: lead.name ?? '', role: lead.role ?? '', company: lead.company ?? '', notes: lead.notes ?? '' })
  const [status, setStatus] = useState<LeadStatus>(lead.status)
  const dirty = form.name !== (lead.name ?? '') || form.role !== (lead.role ?? '') || form.company !== (lead.company ?? '') || form.notes !== (lead.notes ?? '') || status !== lead.status

  function save() {
    patch.mutate({ id: lead.id, patch: { ...form, status } }, {
      onSuccess: () => toast.success('Saved'),
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not save'),
    })
  }

  return (
    <div className="space-y-5 px-4 pb-8" data-testid="lead-drawer">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm break-all">{lead.email}</span>
        <Button variant="ghost" size="icon-xs" aria-label="Copy email address" onClick={() => void navigator.clipboard.writeText(lead.email).then(() => toast.success('Copied'))}>
          <Copy aria-hidden />
        </Button>
        <ConfidenceBadge value={lead.email_confidence} />
      </div>

      <div className="grid gap-3">
        {(['name', 'role', 'company'] as const).map((f) => (
          <label key={f} className="grid gap-1 text-sm">
            <span className="text-muted-foreground text-xs capitalize">{f}</span>
            <Input value={form[f]} onChange={(e) => setForm({ ...form, [f]: e.target.value })} />
          </label>
        ))}
        <div className="grid gap-1 text-sm">
          <span className="text-muted-foreground text-xs" id="status-label">Status</span>
          <Select value={status} onValueChange={(v) => setStatus(v as LeadStatus)}>
            <SelectTrigger aria-labelledby="status-label" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>{STATUS_OPTIONS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <label className="grid gap-1 text-sm">
          <span className="text-muted-foreground text-xs">Notes</span>
          <Textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </label>
        <Button onClick={save} disabled={!dirty || patch.isPending} className="w-fit">Save changes</Button>
      </div>

      <section aria-label="Source" className="space-y-1.5">
        <h3 className="text-sm font-medium">Where this came from</h3>
        {safeHttpUrl(lead.source_url) ? (
          <a href={safeHttpUrl(lead.source_url)?.href} target="_blank" rel="noreferrer noopener" className="text-primary flex items-center gap-1 text-sm break-all hover:underline">
            {lead.source_url}<ExternalLink className="size-3 shrink-0" aria-label="opens in your browser" />
          </a>
        ) : <p className="text-muted-foreground text-sm">Imported from a file (no source page).</p>}
        {lead.context_snippet ? <blockquote className="text-muted-foreground border-l-2 pl-3 text-sm italic">“{lead.context_snippet}”</blockquote> : null}
        {lead.run ? (
          <p className="text-muted-foreground text-xs">
            Found by search #{lead.run.id}: <Link className="hover:underline" to={`/leads?run=${lead.run.id}`}>{lead.run.request_text}</Link>
          </p>
        ) : null}
        {lead.notes?.startsWith('Guessed') ? <p className="text-xs text-amber-700 dark:text-amber-300">{lead.notes}</p> : null}
      </section>

      {lead.score !== null && lead.score !== undefined ? (
        <section aria-label="Score" className="space-y-1.5">
          <h3 className="flex items-center gap-2 text-sm font-medium">Score <ScoreBadge value={lead.score} /></h3>
          <ul className="space-y-0.5 text-sm">
            {lead.score_reasons.map((r) => (
              <li key={r.label} className="flex gap-2"><span className="text-muted-foreground w-9 shrink-0 text-right text-xs tabular-nums">+{r.points}</span><span>{r.detail}</span></li>
            ))}
          </ul>
        </section>
      ) : null}

      {lead.sources.length > 1 ? (
        <section aria-label="Sources" className="space-y-1.5">
          <h3 className="text-sm font-medium">Seen on {lead.sources.length} pages</h3>
          <ul className="space-y-1 text-xs">
            {lead.sources.map((s) => {
              const u = safeHttpUrl(s.source_url)
              return (
                <li key={s.source_url} className="break-all">
                  {u ? <a href={u.href} target="_blank" rel="noreferrer noopener" className="text-primary hover:underline">{s.source_url}</a> : s.source_url}
                  <span className="text-muted-foreground"> {[s.page_type, s.page_tier].filter(Boolean).join(' · ')}</span>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}

      {lead.trace.length > 0 ? (
        <section aria-label="Why was this found" className="space-y-1.5">
          <details>
            <summary className="cursor-pointer text-sm font-medium">Why was this found?</summary>
            <ol className="mt-2 space-y-1.5 border-l pl-3">
              {lead.trace.map((t, i) => (
                <li key={i} className="text-xs"><span className="text-muted-foreground mr-1.5 uppercase">{t.kind}</span>{t.message}</li>
              ))}
            </ol>
          </details>
        </section>
      ) : null}

      <section aria-label="Timeline" className="space-y-1.5">
        <h3 className="text-sm font-medium">Activity</h3>
        {lead.timeline.length === 0 ? <p className="text-muted-foreground text-sm">Nothing yet. Sent emails, replies and bounces will appear here.</p> : (
          <ol className="space-y-2 border-l pl-3">
            {lead.timeline.map((t, i) => (
              <li key={i} className="text-sm">
                <span className="font-medium">{EVENT_LABEL[t.type] ?? t.type}</span>
                <span className="text-muted-foreground ml-2 text-xs">{relativeTime(t.at)}</span>
                {t.detail ? <p className="text-muted-foreground text-xs">{t.detail}</p> : null}
              </li>
            ))}
          </ol>
        )}
      </section>
      <p className="text-muted-foreground text-xs">Added {relativeTime(lead.created_at)}</p>
    </div>
  )
}
