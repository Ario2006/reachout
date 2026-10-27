import { AlertTriangle, FileText, Loader2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError } from '@/lib/api/client'
import { useUpdateCampaign } from '@/lib/api/hooks-campaigns'
import type { Campaign, CampaignIn } from '@/lib/api/types'
import { FollowupFields, validateFollowups, type FollowupDraft } from './followups-fields'
import { TemplatePicker } from './followups-template-picker'
import { capWarning } from './helpers'

interface Draft {
  name: string
  dailyCap: string
  windowStart: string
  windowEnd: string
  delayMin: string
  delayMax: string
  attachment: string
  templateId: string
  followups: FollowupDraft
}

const daysOf = (c: Campaign): number[] => c.followup_days ?? [4, 9]

function fromCampaign(c: Campaign): Draft {
  return {
    name: c.name, dailyCap: String(c.daily_cap), windowStart: c.window_start, windowEnd: c.window_end,
    delayMin: c.delay_min_s === null || c.delay_min_s === undefined ? '' : String(c.delay_min_s),
    delayMax: c.delay_max_s === null || c.delay_max_s === undefined ? '' : String(c.delay_max_s),
    attachment: c.attachment_path ?? '',
    templateId: c.template_id === null || c.template_id === undefined ? '' : String(c.template_id),
    followups: {
      enabled: c.followups_enabled, day1: String(daysOf(c)[0] ?? 4), day2: String(daysOf(c)[1] ?? 9), requireApproval: c.followups_require_approval,
    },
  }
}

/** The request body, or the first problem found. Mirrors the backend's validation so errors show before saving. */
export function toRequest(c: Campaign, d: Draft): { body: CampaignIn } | { error: string } {
  const cap = Number(d.dailyCap)
  if (!d.name.trim()) return { error: 'Give the campaign a name.' }
  if (!Number.isInteger(cap) || cap < 1 || cap > 100) return { error: 'The daily cap must be a whole number from 1 to 100.' }
  const lo = d.delayMin.trim() === '' ? null : Number(d.delayMin)
  const hi = d.delayMax.trim() === '' ? null : Number(d.delayMax)
  if ((lo !== null && (!Number.isInteger(lo) || lo < 0)) || (hi !== null && (!Number.isInteger(hi) || hi < 0))) return { error: 'Delays are whole seconds.' }
  if (lo !== null && hi !== null && hi < lo) return { error: 'The longest delay must not be shorter than the shortest.' }
  const fu = validateFollowups(d.followups, daysOf(c))
  if ('error' in fu) return { error: fu.error }
  return {
    body: {
      name: d.name.trim(), template_id: d.templateId === '' ? null : Number(d.templateId), mode: c.mode ?? null, attachment_path: d.attachment.trim() || null,
      daily_cap: cap, window_start: d.windowStart, window_end: d.windowEnd, delay_min_s: lo, delay_max_s: hi,
      followups_enabled: d.followups.enabled, followup_days: fu.days, followups_require_approval: d.followups.requireApproval,
    },
  }
}

export function SettingsForm({ campaign }: { campaign: Campaign }) {
  const [d, setD] = useState<Draft>(() => fromCampaign(campaign))
  const [formError, setFormError] = useState<string | null>(null)
  const save = useUpdateCampaign(campaign.id)
  const locked = campaign.state === 'running' || campaign.state === 'done'
  useEffect(() => { setD(fromCampaign(campaign)) }, [campaign])
  const dirty = JSON.stringify(d) !== JSON.stringify(fromCampaign(campaign))
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((p) => ({ ...p, [k]: v }))
  const warn = capWarning(Number(d.dailyCap))
  const followupError = (() => { const r = validateFollowups(d.followups, daysOf(campaign)); return 'error' in r ? r.error : null })()
  const bridge = typeof window !== 'undefined' ? window.reachout : undefined

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const r = toRequest(campaign, d)
    if ('error' in r) { setFormError(r.error); return }
    setFormError(null)
    save.mutate(r.body, {
      onSuccess: () => toast.success('Campaign settings saved'),
      onError: (err) => setFormError(err instanceof ApiError ? err.detail : 'Could not save. Try again.'),
    })
  }

  const pick = async () => {
    try {
      const p = await bridge?.pickPdf()
      if (p) set('attachment', p)
    } catch {
      toast.error('Could not open the file dialog. Paste the path instead.')
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Campaign settings">
      {locked ? (
        <Alert><AlertTriangle aria-hidden /><AlertDescription>
          {campaign.state === 'running' ? 'Settings are locked while the campaign runs. Pause it to edit them.' : 'This campaign is finished; its settings are read-only.'}
        </AlertDescription></Alert>
      ) : null}
      <fieldset disabled={locked || save.isPending} className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="c-name">Name</Label>
          <Input id="c-name" value={d.name} onChange={(e) => set('name', e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="c-cap">Daily cap</Label>
          <Input id="c-cap" inputMode="numeric" value={d.dailyCap} onChange={(e) => set('dailyCap', e.target.value)} aria-describedby="c-cap-help" />
          <p id="c-cap-help" className={warn ? 'text-amber-700 text-xs dark:text-amber-400' : 'text-muted-foreground text-xs'}>
            {warn ?? `Emails per day for all campaigns together (recommended up to 25, ceiling 100).`}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1.5">
            <Label htmlFor="c-ws">Window starts</Label>
            <Input id="c-ws" type="time" value={d.windowStart} onChange={(e) => set('windowStart', e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="c-we">Window ends</Label>
            <Input id="c-we" type="time" value={d.windowEnd} onChange={(e) => set('windowEnd', e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="c-dmin">Shortest delay (seconds)</Label>
          <Input id="c-dmin" inputMode="numeric" placeholder="Use Settings" value={d.delayMin} onChange={(e) => set('delayMin', e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="c-dmax">Longest delay (seconds)</Label>
          <Input id="c-dmax" inputMode="numeric" placeholder="Use Settings" value={d.delayMax} onChange={(e) => set('delayMax', e.target.value)} />
        </div>
        <TemplatePicker value={d.templateId} onChange={(v) => set('templateId', v)} mode={campaign.mode} followupsEnabled={d.followups.enabled} />
        <FollowupFields value={d.followups} onChange={(v) => set('followups', v)} error={followupError} />
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="c-att">Attachment (one PDF, optional)</Label>
          <div className="flex gap-2">
            <Input id="c-att" value={d.attachment} onChange={(e) => set('attachment', e.target.value)} placeholder="/Users/you/Documents/cv.pdf" className="font-mono text-xs" />
            {bridge ? <Button type="button" variant="outline" onClick={() => void pick()}><FileText aria-hidden /> Choose…</Button> : null}
            {d.attachment ? <Button type="button" variant="ghost" size="icon" aria-label="Remove attachment" onClick={() => set('attachment', '')}><X aria-hidden /></Button> : null}
          </div>
          <p className="text-muted-foreground text-xs">Files over 2 MB make cold emails look suspicious and may be filtered.</p>
        </div>
      </fieldset>

      {campaign.warnings.length > 0 ? (
        <Alert><AlertTriangle aria-hidden /><AlertDescription>
          <ul className="list-disc space-y-1 pl-4">{campaign.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </AlertDescription></Alert>
      ) : null}
      {formError ? <p role="alert" className="text-destructive text-sm">{formError}</p> : null}
      {locked ? null : (
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!dirty || save.isPending || followupError !== null}>{save.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Save settings</Button>
          {dirty ? <span className="text-muted-foreground text-xs">Unsaved changes</span> : null}
        </div>
      )}
    </form>
  )
}
