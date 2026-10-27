import { Loader2, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError } from '@/lib/api/client'
import { useCampaignList, useGenerateDrafts } from '@/lib/api/hooks-compose'
import { TERMINAL, useLive } from '@/lib/jobs-store'
import { pluralize } from '@/lib/format'

/** "Generate drafts for selection" for the Leads bulk bar. Explicit lead ids only. */
export function GenerateForSelection({ ids, disabled }: { ids: number[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(true)}><Sparkles aria-hidden /> Generate drafts</Button>
      {open ? <GenerateDialog ids={ids} onClose={() => setOpen(false)} /> : null}
    </>
  )
}

function GenerateDialog({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const [campaignId, setCampaignId] = useState('')
  const [jobId, setJobId] = useState<number | null>(null)
  const campaigns = useCampaignList()
  const gen = useGenerateDrafts()
  const job = useLive((s) => (jobId === null ? undefined : s.jobs[jobId]))
  const usable = (campaigns.data ?? []).filter((c) => c.template_id !== null && (c.state === 'draft' || c.state === 'paused'))
  const active = jobId !== null && (!job || !TERMINAL.has(job.state))

  async function start() {
    try {
      const j = await gen.mutateAsync({ campaignId: Number(campaignId), leadIds: ids })
      setJobId(j.id)
    } catch { /* gen.error shown below */ }
  }

  return (
    <>
      <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Generate drafts for {pluralize(ids.length, 'lead')}</DialogTitle>
            <DialogDescription>Suppressed leads and leads that already replied, bounced or opted out are skipped. Nothing is sent; you review each draft first.</DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            <Label htmlFor="gen-campaign">Campaign</Label>
            <Select value={campaignId} onValueChange={setCampaignId} disabled={active}>
              <SelectTrigger id="gen-campaign" aria-label="Campaign for the drafts"><SelectValue placeholder={campaigns.isPending ? 'Loading campaigns…' : 'Choose a campaign'} /></SelectTrigger>
              <SelectContent>{usable.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}</SelectContent>
            </Select>
            {!campaigns.isPending && usable.length === 0 ? <p className="text-muted-foreground text-xs">No campaign with a template yet. Create one on the Compose screen.</p> : null}
          </div>
          {gen.error ? <Alert variant="destructive" role="alert"><AlertDescription>{gen.error instanceof ApiError ? gen.error.detail : 'Could not start.'}</AlertDescription></Alert> : null}
          {jobId !== null ? (
            <p className="text-sm" aria-live="polite" data-testid="selection-progress">
              {job ? (job.state === 'failed' ? `Failed: ${job.error ?? 'unknown error'}` : job.message ?? 'Working…') : 'Queued…'}
            </p>
          ) : null}
          <DialogFooter>
            {jobId !== null && !active ? <Button asChild><Link to={`/compose?campaign=${campaignId}`}>Review drafts</Link></Button> : null}
            <Button variant="ghost" onClick={onClose}>Close</Button>
            {jobId === null ? (
              <Button disabled={!campaignId || gen.isPending} onClick={() => void start()}>
                {gen.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Generate
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
