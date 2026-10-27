import { CheckCheck, Loader2, Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useBulkApprove, useDrafts, usePatchDraft, type DraftItem, type DraftKindFilter } from '@/lib/api/hooks-compose'
import type { Campaign, DraftStatus } from '@/lib/api/types'
import { pluralize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { DraftDetail } from './draft-detail'
import { DraftStatusBadge, FlagBadges } from './flag-badges'
import { GenerateControls } from './generate-controls'
import { useReviewKeys } from './use-review-keys'

type Tab = 'review' | 'approved' | 'rejected' | 'all'
const TABS: { id: Tab; label: string; status: DraftStatus[] }[] = [
  { id: 'review', label: 'To review', status: ['pending', 'needs_review'] },
  { id: 'approved', label: 'Approved', status: ['approved'] },
  { id: 'rejected', label: 'Rejected', status: ['rejected'] },
  { id: 'all', label: 'All', status: [] },
]
const KINDS: { id: DraftKindFilter; label: string }[] = [{ id: 'initial', label: 'Initial' }, { id: 'followups', label: 'Follow-ups' }]
const KIND_NAME = { initial: 'Initial', followup_1: 'Follow-up 1', followup_2: 'Follow-up 2' } as const
const OPEN: ReadonlySet<DraftStatus> = new Set(['pending', 'needs_review'])

export function DraftsPanel({ campaign }: { campaign: Campaign }) {
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>('review')
  const [kind, setKind] = useState<DraftKindFilter>(() => (params.get('kind') === 'followups' ? 'followups' : 'initial'))
  const [q, setQ] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [overrides, setOverrides] = useState<Record<number, DraftStatus>>({})
  const [confirmBulk, setConfirmBulk] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const status = TABS.find((t) => t.id === tab)?.status ?? []
  const drafts = useDrafts({ campaignId: campaign.id, status, q, kind })
  const patch = usePatchDraft()
  const bulk = useBulkApprove()
  const items = useMemo(() => drafts.data?.items ?? [], [drafts.data])
  const counts = drafts.data?.counts
  const noTemplate = campaign.template_id === null

  const effective = useCallback((d: DraftItem): DraftStatus => overrides[d.id] ?? d.status, [overrides])
  // Drop an override once the server agrees with it (or the row left the list).
  useEffect(() => {
    setOverrides((o) => {
      const keep = Object.entries(o).filter(([id, s]) => items.some((d) => d.id === Number(id) && d.status !== s))
      return keep.length === Object.keys(o).length ? o : Object.fromEntries(keep)
    })
  }, [items])

  const firstOpen = items.find((d) => OPEN.has(effective(d)))
  const selected = items.find((d) => d.id === selectedId) ?? firstOpen ?? items[0] ?? null
  const selIndex = selected ? items.indexOf(selected) : -1
  const editing = selected !== null && editingId === selected.id

  // Keep the keyboard-selected row in view.
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(() => {
    listRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [selected?.id])

  const nextOpenAfter = useCallback((index: number): DraftItem | null => {
    for (let i = 1; i <= items.length; i++) {
      const d = items[(index + i) % items.length]
      if (d && d.id !== items[index]?.id && OPEN.has(effective(d))) return d
    }
    return null
  }, [items, effective])

  const decide = useCallback((d: DraftItem, next: 'approved' | 'rejected') => {
    const advance = nextOpenAfter(items.indexOf(d))
    setOverrides((o) => ({ ...o, [d.id]: next }))
    if (advance) setSelectedId(advance.id)
    patch.mutate({ id: d.id, patch: { status: next } }, {
      onError: (e) => {
        setOverrides((o) => Object.fromEntries(Object.entries(o).filter(([id]) => Number(id) !== d.id)))
        setSelectedId(d.id)
        toast.error(e instanceof ApiError ? e.detail : 'The change was not saved')
      },
    })
  }, [items, nextOpenAfter, patch])

  const move = useCallback((delta: number) => {
    if (items.length === 0) return
    const i = Math.min(items.length - 1, Math.max(0, selIndex + delta))
    const d = items[i]
    if (d) setSelectedId(d.id)
  }, [items, selIndex])

  useReviewKeys({
    enabled: selected !== null && !editing, // never approve, reject or move while the text is being edited
    onApprove: () => { if (selected && effective(selected) !== 'approved') decide(selected, 'approved') },
    onReject: () => { if (selected && effective(selected) !== 'rejected') decide(selected, 'rejected') },
    onNext: () => move(1),
    onPrev: () => move(-1),
  })

  const unflagged = items.filter((d) => effective(d) === 'pending' && d.flags.length === 0).length
  const pendingTotal = counts?.pending ?? 0
  const complete = (drafts.data?.total ?? 0) <= items.length
  async function approveAll() {
    try {
      const r = await bulk.mutateAsync(campaign.id)
      toast.success(`${pluralize(r.approved, 'draft')} approved${r.skipped_flagged ? `, ${r.skipped_flagged} flagged left for you` : ''}`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Approving failed')
    }
    setConfirmBulk(false)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {noTemplate ? <Alert><AlertDescription>This campaign has no template yet. Pick one in the header before generating drafts.</AlertDescription></Alert> : null}
      <GenerateControls campaignId={campaign.id} disabled={noTemplate} />

      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Draft filters">
        <div role="group" aria-label="Draft type" className="flex gap-1">
          {KINDS.map((k) => (
            <Button key={k.id} size="sm" variant={kind === k.id ? 'secondary' : 'ghost'} aria-pressed={kind === k.id} onClick={() => { setKind(k.id); setSelectedId(null) }}>{k.label}</Button>
          ))}
        </div>
        <div role="group" aria-label="Show drafts" className="flex gap-1">
          {TABS.map((t) => (
            <Button key={t.id} size="sm" variant={tab === t.id ? 'secondary' : 'ghost'} aria-pressed={tab === t.id} onClick={() => setTab(t.id)}>
              {t.label}
              {counts && t.id !== 'all' ? <span className="text-muted-foreground text-xs tabular-nums">{t.id === 'review' ? counts.pending + counts.needs_review : counts[t.id]}</span> : null}
            </Button>
          ))}
        </div>
        <div className="relative">
          <Search className="text-muted-foreground absolute top-2 left-2 size-4" aria-hidden />
          <Input aria-label="Search drafts" placeholder="Search drafts" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 w-48 pl-8" />
        </div>
        <Button size="sm" variant="outline" className="ml-auto" disabled={pendingTotal === 0 || bulk.isPending} onClick={() => setConfirmBulk(true)}>
          <CheckCheck aria-hidden /> Approve all unflagged
        </Button>
      </div>

      {drafts.isPending ? (
        <div className="space-y-2" aria-label="Loading drafts" data-testid="drafts-loading">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : drafts.isError ? (
        <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-3">
          {drafts.error instanceof ApiError ? drafts.error.detail : 'Could not load drafts.'}
          <Button size="xs" variant="outline" onClick={() => void drafts.refetch()}>Retry</Button>
        </AlertDescription></Alert>
      ) : items.length === 0 ? (
        <EmptyState tab={tab} kind={kind} hasAny={(counts ? counts.pending + counts.needs_review + counts.approved + counts.rejected : 0) > 0} q={q} />
      ) : (
        <div className="grid min-h-[420px] flex-1 grid-cols-[minmax(240px,320px)_1fr] overflow-hidden rounded-lg border">
          <ul ref={listRef} className="divide-y overflow-y-auto" aria-label="Drafts" data-testid="draft-list">
            {items.map((d) => {
              const st = effective(d)
              const active = d.id === selected?.id
              return (
                <li key={d.id}>
                  <button
                    type="button"
                    aria-current={active}
                    onClick={() => setSelectedId(d.id)}
                    data-testid="draft-row"
                    className={cn('hover:bg-muted/60 focus-visible:ring-ring w-full space-y-1 px-3 py-2 text-left outline-none focus-visible:ring-2', active && 'bg-muted', !OPEN.has(st) && 'opacity-70')}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{d.lead.name ?? d.lead.email}</span>
                      <DraftStatusBadge status={st} />
                    </div>
                    <p className="text-muted-foreground truncate text-xs">{d.kind !== 'initial' ? `${KIND_NAME[d.kind]} · ` : ''}{d.lead.company ?? d.lead.email}{d.subject ? ` · ${d.subject}` : ''}</p>
                    {d.flags.length > 0 ? <div className="flex flex-wrap items-center gap-1"><FlagBadges flags={d.flags} max={2} /></div> : null}
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="flex min-h-0 flex-col border-l">
            {selected ? (
              <DraftDetail key={selected.id} item={selected} status={effective(selected)} editing={editing} onEditingChange={(e) => setEditingId(e ? selected.id : null)} onApprove={() => decide(selected, 'approved')} onReject={() => decide(selected, 'rejected')} />
            ) : null}
          </div>
        </div>
      )}
      {items.some((d) => d.flags.some((f) => f.code === 'missing_field')) ? (
        <p className="text-xs text-amber-600" role="status" data-testid="missing-hint">Some drafts have empty merge fields. Fill your profile fields on the Profile tab, then rewrite those drafts.</p>
      ) : null}
      <p className="text-muted-foreground text-xs">Keyboard: <kbd>A</kbd> approve, <kbd>R</kbd> reject, <kbd>J</kbd> next, <kbd>K</kbd> previous. Nothing is sent until you start a campaign.</p>

      <Dialog open={confirmBulk} onOpenChange={setConfirmBulk}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve all unflagged drafts?</DialogTitle>
            <DialogDescription>
              {complete && tab === 'review' ? `${pluralize(unflagged, 'draft')} without flags will be approved. ` : 'Every to-review draft without flags will be approved. '}
              Drafts with flags and drafts that need review stay in the queue for you to read.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmBulk(false)}>Cancel</Button>
            <Button onClick={() => void approveAll()} disabled={bulk.isPending}>
              {bulk.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function EmptyState({ tab, kind, hasAny, q }: { tab: Tab; kind: DraftKindFilter; hasAny: boolean; q: string }) {
  const text = q ? 'No drafts match your search.'
    : kind === 'followups' && !hasAny ? 'No follow-up drafts yet. They are written when a follow-up comes due and this campaign requires your approval.'
    : !hasAny ? 'No drafts yet. Choose Generate drafts to write one for every lead that fits this campaign.'
      : tab === 'review' ? 'All caught up. Every draft has been approved or rejected.'
        : 'Nothing here yet.'
  return <div className="text-muted-foreground flex flex-1 items-center justify-center rounded-lg border border-dashed p-10 text-center text-sm" data-testid="drafts-empty">{text}</div>
}
