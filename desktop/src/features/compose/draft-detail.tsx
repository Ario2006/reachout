import { Check, Loader2, Pencil, RefreshCw, X } from 'lucide-react'
import { useState } from 'react'
import { ConfidenceBadge } from '@/components/lead-badges'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useDraft, usePatchDraft, useRegenerateOpener, type DraftItem } from '@/lib/api/hooks-compose'
import type { DraftStatus } from '@/lib/api/types'
import { safeHttpUrl } from '@/lib/format'
import { DraftStatusBadge, FlagBadges } from './flag-badges'
import { HighlightedText } from './highlighted-text'

interface Props {
  item: DraftItem
  status: DraftStatus
  onApprove: () => void
  onReject: () => void
}

const kbd = 'bg-muted text-muted-foreground ml-1 rounded border px-1 font-mono text-[10px]'

export function DraftDetail({ item, status, onApprove, onReject }: Props) {
  const detail = useDraft(item.id)
  const patch = usePatchDraft()
  const regen = useRegenerateOpener()
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [showSent, setShowSent] = useState(false)
  const d = detail.data ?? item
  const source = safeHttpUrl(d.lead.source_url)
  const error = patch.error ?? regen.error

  function startEdit() {
    setSubject(d.subject)
    setBody(d.body)
    setEditing(true)
  }
  async function save() {
    try {
      await patch.mutateAsync({ id: d.id, patch: { subject, body } })
      setEditing(false)
    } catch { /* shown inline from patch.error */ }
  }

  return (
    <article className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4" aria-label={`Draft for ${d.lead.name ?? d.lead.email}`} data-testid="draft-detail">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">{d.lead.name ?? d.lead.email}</h2>
          <DraftStatusBadge status={status} />
          <ConfidenceBadge value={d.lead.email_confidence} />
        </div>
        <p className="text-muted-foreground text-sm">
          <span className="font-mono text-xs">{d.lead.email}</span>
          {d.lead.role || d.lead.company ? ` · ${[d.lead.role, d.lead.company].filter(Boolean).join(' at ')}` : ''}
          {source ? <> · <a className="underline" href={source.href} target="_blank" rel="noreferrer">source</a></> : null}
        </p>
        {d.flags.length > 0 ? <div className="flex flex-wrap gap-1.5"><FlagBadges flags={d.flags} /></div> : null}
      </header>

      {status === 'needs_review' ? (
        <Alert><AlertDescription>The model could not write a safe opener, so a neutral one was used. Read it, then approve it yourself or reject it. It is never approved in bulk.</AlertDescription></Alert>
      ) : null}
      {error ? <Alert variant="destructive" role="alert"><AlertDescription>{error instanceof ApiError ? error.detail : 'Something went wrong.'}</AlertDescription></Alert> : null}

      {editing ? (
        <div className="space-y-2">
          <Input aria-label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
          <Textarea aria-label="Email body" value={body} onChange={(e) => setBody(e.target.value)} rows={12} className="font-mono text-sm"
            onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void save() }} />
          {status === 'approved' ? <p className="text-muted-foreground text-xs">This draft is approved. Saving a change withdraws the approval; approve it again afterwards.</p> : null}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void save()} disabled={patch.isPending || !subject.trim() || !body.trim()}>
              {patch.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Save changes
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm"><span className="text-muted-foreground">Subject: </span><span className="font-medium" data-testid="draft-subject">{d.subject}</span></p>
          <div className="bg-card rounded-lg border p-3">
            {showSent && detail.data
              ? <HighlightedText text={detail.data.final_text} span={d.opener_span} />
              : <HighlightedText text={d.body} span={d.opener_span} />}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            {d.opener_span ? <span className="text-muted-foreground"><mark className="bg-primary/15 rounded-sm px-1">Highlighted</mark> sentence was written by the local model.</span>
              : d.opener_source === 'manual' ? <span className="text-muted-foreground">The opener was edited by hand.</span> : null}
            <Button variant="link" size="xs" onClick={() => setShowSent((v) => !v)} disabled={!detail.data}>{showSent ? 'Show draft' : 'Show as sent (with footer)'}</Button>
          </div>
        </div>
      )}

      {d.lead.context_snippet ? (
        <details className="text-sm">
          <summary className="text-muted-foreground cursor-pointer">Source text the opener is based on</summary>
          <p className="bg-muted/50 mt-2 rounded-md p-2 text-xs whitespace-pre-wrap">{d.lead.context_snippet}</p>
        </details>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-3">
        <Button size="sm" onClick={onApprove} disabled={status === 'approved' || editing}>
          <Check aria-hidden /> {status === 'needs_review' ? 'Approve anyway' : 'Approve'}<span className={kbd} aria-hidden>A</span>
        </Button>
        <Button size="sm" variant="outline" onClick={onReject} disabled={status === 'rejected' || editing}>
          <X aria-hidden /> Reject<span className={kbd} aria-hidden>R</span>
        </Button>
        <Button size="sm" variant="outline" onClick={() => regen.mutate(d.id)} disabled={regen.isPending || editing}>
          {regen.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
          {regen.isPending ? 'Writing a new opener…' : 'Regenerate opener'}
        </Button>
        {!editing ? <Button size="sm" variant="ghost" onClick={startEdit}><Pencil aria-hidden /> Edit</Button> : null}
      </div>
    </article>
  )
}
