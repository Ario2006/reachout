import { Loader2, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useProfile, usePutProfile } from '@/lib/api/hooks-compose'
import type { ProfileField } from '@/lib/api/types'

const KEY_RE = /^[a-z][a-z0-9_]*$/
const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^[^a-z]+/, '').slice(0, 40)

export function ProfilePanel() {
  const profile = useProfile()
  const put = usePutProfile()
  const [fields, setFields] = useState<ProfileField[]>([])
  useEffect(() => { if (profile.data) setFields(profile.data.fields) }, [profile.data])

  if (profile.isPending) return <div className="space-y-2" role="status" aria-label="Loading profile"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
  if (profile.isError) {
    return <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-3">
      {profile.error instanceof ApiError ? profile.error.detail : 'Could not load your profile.'}
      <Button size="xs" variant="outline" onClick={() => void profile.refetch()}>Retry</Button>
    </AlertDescription></Alert>
  }
  const reserved = profile.data.reserved
  const keys = fields.map((f) => f.key)
  const problem = (f: ProfileField, i: number): string | null =>
    !KEY_RE.test(f.key) ? 'Use lowercase letters, digits and underscores, starting with a letter.'
      : reserved.includes(f.key) ? 'This name is filled in automatically.'
        : keys.indexOf(f.key) !== i ? 'Another field already uses this name.' : null
  const invalid = fields.some((f, i) => problem(f, i) !== null || !f.label.trim())
  const dirty = JSON.stringify(fields) !== JSON.stringify(profile.data.fields)
  const patch = (i: number, p: Partial<ProfileField>) => setFields(fields.map((f, j) => (j === i ? { ...f, ...p } : f)))

  return (
    <div className="max-w-2xl space-y-4" data-testid="profile-panel">
      <p className="text-muted-foreground text-sm">Facts about you that templates can use, like <code>{'{{my_summary}}'}</code>. Empty fields are flagged on the drafts that use them. Built in and filled automatically: {reserved.map((r) => `{{${r}}}`).join(', ')}.</p>
      {fields.map((f, i) => {
        const err = problem(f, i)
        return (
          <div key={i} className="space-y-1.5 rounded-lg border p-3" data-testid="profile-field">
            <div className="flex gap-2">
              <Input aria-label="Field label" value={f.label} maxLength={80} placeholder="Label" onChange={(e) => patch(i, { label: e.target.value, ...(f.key === '' || f.key === slug(f.label) ? { key: slug(e.target.value) } : {}) })} />
              <Input aria-label="Field name" className="font-mono" value={f.key} maxLength={40} placeholder="field_name" onChange={(e) => patch(i, { key: e.target.value })} aria-invalid={err !== null} />
              <Button variant="ghost" size="icon" aria-label={`Remove ${f.label || 'field'}`} onClick={() => setFields(fields.filter((_, j) => j !== i))}><Trash2 aria-hidden /></Button>
            </div>
            {err ? <p className="text-destructive text-xs" role="alert">{err}</p> : null}
            <Textarea aria-label={`${f.label || 'Field'} value`} rows={3} maxLength={2000} value={f.value} onChange={(e) => patch(i, { value: e.target.value })} />
          </div>
        )
      })}
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => setFields([...fields, { key: '', label: '', value: '' }])}><Plus aria-hidden /> Add field</Button>
        <Button
          disabled={!dirty || invalid || put.isPending}
          onClick={() => put.mutate(fields, { onSuccess: () => toast.success('Profile saved') })}
        >
          {put.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Save profile
        </Button>
      </div>
      {put.error ? <Alert variant="destructive" role="alert"><AlertDescription>{put.error instanceof ApiError ? put.error.detail : 'Could not save.'}</AlertDescription></Alert> : null}
    </div>
  )
}
