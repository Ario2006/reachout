import { Plus, Send } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useCampaigns, useCreateCampaign } from '@/lib/api/hooks-campaigns'
import type { Campaign } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { CampaignDetail } from './campaign-detail'
import { StateBadge } from './campaign-status'
import { progressOf } from './helpers'

function NewCampaign({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const create = useCreateCampaign()
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) { setError('Give the campaign a name.'); return }
    create.mutate({ name: name.trim(), daily_cap: 25, window_start: '09:00', window_end: '18:00', followups_enabled: false, followups_require_approval: false }, {
      onSuccess: (c) => { setName(''); setError(null); onCreated(c.id) },
      onError: (err) => setError(err instanceof ApiError ? err.detail : 'Could not create the campaign.'),
    })
  }
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New campaign</DialogTitle>
          <DialogDescription>Drafts are generated and approved in Compose; come back here to test, dry run and start.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="new-name">Name</Label>
            <Input id="new-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          {error ? <p role="alert" className="text-destructive text-sm">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={create.isPending}>Create</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function CampaignRow({ c, selected, onSelect }: { c: Campaign; selected: boolean; onSelect: () => void }) {
  const p = progressOf(c.counts)
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={cn('hover:bg-muted/60 focus-visible:ring-ring w-full rounded-lg px-3 py-2.5 text-left outline-none focus-visible:ring-2', selected && 'bg-muted')}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{c.name}</span>
          <StateBadge state={c.state} />
        </div>
        <div className="text-muted-foreground mt-0.5 text-xs">
          {c.state === 'draft' ? `${c.counts.drafts_approved} approved` : `${p.sent} / ${p.total} sent`}
        </div>
      </button>
    </li>
  )
}

export function CampaignsPage() {
  const [sp, setSp] = useSearchParams()
  const list = useCampaigns()
  const [creating, setCreating] = useState(false)
  const selected = Number(sp.get('c')) || null
  const select = (id: number | null) => setSp(id === null ? {} : { c: String(id) }, { replace: true })
  const items = list.data ?? []
  const active = selected ?? items[0]?.id ?? null

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-r p-4" aria-label="Campaigns">
        <div className="flex items-center justify-between">
          <h1 className="text-base font-semibold">Campaigns</h1>
          <Button size="sm" onClick={() => setCreating(true)}><Plus aria-hidden /> New</Button>
        </div>
        {list.isLoading ? (
          <div className="space-y-2" aria-label="Loading campaigns"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : list.error ? (
          <div role="alert" className="space-y-2 text-sm">
            <p className="text-destructive">Could not load campaigns: {list.error instanceof ApiError ? list.error.detail : 'unknown error'}.</p>
            <Button variant="outline" size="sm" onClick={() => void list.refetch()}>Retry</Button>
          </div>
        ) : items.length === 0 ? (
          <div className="text-muted-foreground space-y-2 py-6 text-center text-sm">
            <Send className="mx-auto size-6" aria-hidden />
            <p>No campaigns yet.</p>
            <Button size="sm" onClick={() => setCreating(true)}>Create your first campaign</Button>
          </div>
        ) : (
          <ul className="space-y-1">{items.map((c) => <CampaignRow key={c.id} c={c} selected={c.id === active} onSelect={() => select(c.id)} />)}</ul>
        )}
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto" aria-label="Campaign detail">
        {active !== null ? <CampaignDetail key={active} id={active} /> : (
          <p className="text-muted-foreground p-8 text-sm">Select a campaign, or create one to get started.</p>
        )}
      </main>
      <NewCampaign open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); select(id) }} />
    </div>
  )
}
