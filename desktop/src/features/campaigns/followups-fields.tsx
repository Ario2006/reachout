import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'

export interface FollowupDraft {
  enabled: boolean
  day1: string
  day2: string
  requireApproval: boolean
}

export const MIN_DAY = 1
export const MAX_DAY = 60

/** Parsed days when enabled, or the first problem found. Disabled settings are never validated. */
export function validateFollowups(f: FollowupDraft, fallbackDays: number[]): { days: number[] } | { error: string } {
  const d1 = Number(f.day1)
  const d2 = Number(f.day2)
  const valid = f.day1.trim() !== '' && f.day2.trim() !== '' && Number.isInteger(d1) && Number.isInteger(d2)
  if (!f.enabled) return { days: valid && d1 >= MIN_DAY && d2 <= MAX_DAY && d2 > d1 ? [d1, d2] : fallbackDays }
  if (!valid) return { error: 'Follow-up days are whole numbers of days after the first email.' }
  if (d1 < MIN_DAY || d1 > MAX_DAY || d2 < MIN_DAY || d2 > MAX_DAY) return { error: `Follow-up days must be from ${MIN_DAY} to ${MAX_DAY}.` }
  if (d2 <= d1) return { error: 'Follow-up 2 must come later than follow-up 1.' }
  return { days: [d1, d2] }
}

/** Settings controls for the campaign form. Plain controlled inputs; the form owns saving. */
export function FollowupFields({ value, onChange, error }: { value: FollowupDraft; onChange: (v: FollowupDraft) => void; error: string | null }) {
  const set = <K extends keyof FollowupDraft>(k: K, v: FollowupDraft[K]) => onChange({ ...value, [k]: v })
  return (
    <fieldset className="space-y-3 sm:col-span-2" data-testid="followup-fields">
      <legend className="text-sm font-medium">Follow-ups</legend>
      <div className="flex items-center gap-3">
        <Switch id="fu-enabled" checked={value.enabled} onCheckedChange={(c) => set('enabled', c)} />
        <Label htmlFor="fu-enabled">Send up to two follow-ups to leads who have not replied</Label>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:max-w-md">
        <div className="space-y-1.5">
          <Label htmlFor="fu-day1">Follow-up 1, days after the first email</Label>
          <Input id="fu-day1" inputMode="numeric" value={value.day1} disabled={!value.enabled} aria-invalid={error !== null} aria-describedby="fu-help" onChange={(e) => set('day1', e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="fu-day2">Follow-up 2, days after the first email</Label>
          <Input id="fu-day2" inputMode="numeric" value={value.day2} disabled={!value.enabled} aria-invalid={error !== null} aria-describedby="fu-help" onChange={(e) => set('day2', e.target.value)} />
        </div>
      </div>
      <p id="fu-help" className="text-muted-foreground text-xs">From {MIN_DAY} to {MAX_DAY} days, and follow-up 2 later than follow-up 1. They stop automatically when a lead replies, bounces or opts out.</p>
      <div className="flex items-center gap-3">
        <Switch id="fu-approval" checked={value.requireApproval} disabled={!value.enabled} onCheckedChange={(c) => set('requireApproval', c)} />
        <Label htmlFor="fu-approval">Require my approval before each follow-up is sent</Label>
      </div>
      {error ? <p role="alert" className="text-destructive text-sm" data-testid="followup-error">{error}</p> : null}
    </fieldset>
  )
}
