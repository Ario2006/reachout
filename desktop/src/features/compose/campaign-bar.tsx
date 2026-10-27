import { Loader2, Plus } from 'lucide-react'
import { useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError } from '@/lib/api/client'
import { useCreateCampaign, useSetCampaignTemplate, useTemplates } from '@/lib/api/hooks-compose'
import type { Campaign } from '@/lib/api/types'

interface Props {
  campaigns: Campaign[]
  selectedId: number | null
  onSelect: (id: number) => void
}

/** Pick the campaign drafts belong to, create one, and choose its template. */
export function CampaignBar({ campaigns, selectedId, onSelect }: Props) {
  const templates = useTemplates()
  const setTemplate = useSetCampaignTemplate()
  const current = campaigns.find((c) => c.id === selectedId) ?? null
  const locked = current !== null && current.state !== 'draft' && current.state !== 'paused'

  return (
    <div className="flex flex-wrap items-end gap-3" data-testid="campaign-bar">
      <div className="space-y-1">
        <Label htmlFor="campaign-select" className="text-xs">Campaign</Label>
        <Select value={selectedId === null ? '' : String(selectedId)} onValueChange={(v) => onSelect(Number(v))}>
          <SelectTrigger id="campaign-select" className="w-64" aria-label="Campaign"><SelectValue placeholder="Choose a campaign" /></SelectTrigger>
          <SelectContent>
            {campaigns.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {current ? (
        <div className="space-y-1">
          <Label htmlFor="campaign-template" className="text-xs">Template</Label>
          <Select
            value={current.template_id === null ? '' : String(current.template_id)}
            onValueChange={(v) => setTemplate.mutate({ campaign: current, templateId: Number(v) })}
            disabled={locked || setTemplate.isPending}
          >
            <SelectTrigger id="campaign-template" className="w-64" aria-label="Campaign template"><SelectValue placeholder="Choose a template" /></SelectTrigger>
            <SelectContent>
              {(templates.data ?? []).map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      <NewCampaign onCreated={onSelect} />
      {setTemplate.error ? <p className="text-destructive text-sm" role="alert">{setTemplate.error instanceof ApiError ? setTemplate.error.detail : 'Could not change the template.'}</p> : null}
    </div>
  )
}

function NewCampaign({ onCreated }: { onCreated: (id: number) => void }) {
  const templates = useTemplates()
  const create = useCreateCampaign()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const tpl = templates.data?.find((t) => String(t.id) === templateId)

  async function submit() {
    try {
      const c = await create.mutateAsync({
        name: name.trim(), template_id: tpl ? tpl.id : null, mode: tpl?.mode ?? null, daily_cap: 25, window_start: '09:00', window_end: '18:00',
        followups_enabled: false, followup_days: [4, 9], followups_require_approval: false,
      })
      onCreated(c.id)
      setOpen(false)
      setName('')
      setTemplateId('')
    } catch { /* create.error shows inline */ }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button variant="outline" size="sm"><Plus aria-hidden /> New campaign</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New campaign</DialogTitle>
          <DialogDescription>A campaign groups drafts that use one template. Sending limits are set on the Campaigns screen.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1"><Label htmlFor="new-campaign-name">Name</Label><Input id="new-campaign-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. Summer internships" /></div>
          <div className="space-y-1">
            <Label htmlFor="new-campaign-template">Template</Label>
            <Select value={templateId} onValueChange={setTemplateId}>
              <SelectTrigger id="new-campaign-template" aria-label="Template for the new campaign"><SelectValue placeholder="Choose a template" /></SelectTrigger>
              <SelectContent>{(templates.data ?? []).map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {create.error ? <Alert variant="destructive" role="alert"><AlertDescription>{create.error instanceof ApiError ? create.error.detail : 'Could not create the campaign.'}</AlertDescription></Alert> : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={!name.trim() || !templateId || create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
