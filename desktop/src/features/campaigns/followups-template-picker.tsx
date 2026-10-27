import { AlertTriangle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useTemplates } from '@/lib/api/hooks-compose'
import type { Mode, Template } from '@/lib/api/types'

/** Which follow-up bodies the template lacks for the follow-ups that are switched on. Empty means fine. */
export function missingFollowupBodies(t: Template | undefined, enabled: boolean): string[] {
  if (!t || !enabled) return []
  const out: string[] = []
  if (!t.followup_1_body?.trim()) out.push('follow-up 1')
  if (!t.followup_2_body?.trim()) out.push('follow-up 2')
  return out
}

/** Templates matching the campaign mode first, then the rest. */
export function orderTemplates(all: Template[], mode: Mode | null | undefined): { matching: Template[]; other: Template[] } {
  const matching = all.filter((t) => mode != null && t.mode === mode)
  return { matching, other: all.filter((t) => !matching.includes(t)) }
}

export function TemplatePicker({ value, onChange, mode, followupsEnabled, disabled }: {
  value: string
  onChange: (v: string) => void
  mode: Mode | null | undefined
  followupsEnabled: boolean
  disabled?: boolean
}) {
  const templates = useTemplates()
  const all = templates.data ?? []
  const { matching, other } = orderTemplates(all, mode)
  const chosen = all.find((t) => String(t.id) === value)
  const missing = missingFollowupBodies(chosen, followupsEnabled)
  const opt = (t: Template) => <option key={t.id} value={String(t.id)}>{t.name}{t.mode ? '' : ' (any mode)'}</option>
  return (
    <div className="space-y-1.5 sm:col-span-2" data-testid="template-picker">
      <Label htmlFor="c-template">Template</Label>
      {templates.isPending ? <Skeleton className="h-8 w-full" role="status" aria-label="Loading templates" /> : templates.isError ? (
        <p role="alert" className="text-destructive flex items-center gap-3 text-sm">
          {templates.error instanceof ApiError ? templates.error.detail : 'Could not load templates.'}
          <Button type="button" size="xs" variant="outline" onClick={() => void templates.refetch()}>Retry</Button>
        </p>
      ) : (
        <select
          id="c-template" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} aria-describedby="c-template-help"
          className="border-input bg-background focus-visible:ring-ring/50 h-8 w-full rounded-lg border px-2.5 text-sm outline-none focus-visible:ring-3 disabled:opacity-50"
        >
          <option value="">No template</option>
          {matching.length > 0 ? <optgroup label={`Matches ${mode ?? ''} mode`}>{matching.map(opt)}</optgroup> : null}
          {other.length > 0 ? <optgroup label={matching.length > 0 ? 'Other templates' : 'Templates'}>{other.map(opt)}</optgroup> : null}
        </select>
      )}
      <p id="c-template-help" className="text-muted-foreground text-xs">
        {all.length === 0 && !templates.isPending && !templates.isError ? 'No templates yet. ' : 'The first email and any follow-ups are written from this template. '}
        <Link to="/compose?tab=templates" className="text-foreground underline underline-offset-2">Create or edit templates in Compose</Link>
      </p>
      {missing.length > 0 ? (
        <Alert data-testid="template-followup-warning"><AlertTriangle aria-hidden /><AlertDescription>
          This template has no body for {missing.join(' and ')}. Add {missing.length > 1 ? 'them' : 'it'} in Compose, or choose another template, before those follow-ups can be written.
        </AlertDescription></Alert>
      ) : null}
    </div>
  )
}
