import { Loader2, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useDeleteTemplate, useProfile, useSaveTemplate, useTemplates } from '@/lib/api/hooks-compose'
import type { Mode, Template, TemplateIn } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { TemplatePreview } from './template-preview'

const BLANK: TemplateIn = {
  mode: null, name: '', subject: '', body: 'Hi {{first_name}},\n\n{{custom_opener}}\n\n\n\nThanks,\n{{my_name}}',
  followup_1_body: null, followup_2_body: null,
}
const NO_MODE = 'any'
const toIn = (t: Template): TemplateIn => ({
  mode: t.mode ?? null, name: t.name, subject: t.subject, body: t.body, followup_1_body: t.followup_1_body ?? null, followup_2_body: t.followup_2_body ?? null,
})
const used = (t: TemplateIn): Set<string> => new Set([...`${t.subject}\n${t.body}`.matchAll(/\{\{\s*([A-Za-z_]\w*)/g)].map((m) => m[1] ?? ''))

export function TemplatesPanel() {
  const templates = useTemplates()
  const [selected, setSelected] = useState<number | 'new' | null>(null)
  const list = templates.data ?? []
  const current = selected === 'new' ? null : list.find((t) => t.id === selected) ?? (selected === null ? list[0] ?? null : null)
  const editorKey = selected === 'new' ? 'new' : String(current?.id ?? 'none')

  if (templates.isPending) return <div className="space-y-2" aria-label="Loading templates"><Skeleton className="h-10" /><Skeleton className="h-64" /></div>
  if (templates.isError) {
    return <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-3">
      {templates.error instanceof ApiError ? templates.error.detail : 'Could not load templates.'}
      <Button size="xs" variant="outline" onClick={() => void templates.refetch()}>Retry</Button>
    </AlertDescription></Alert>
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <nav aria-label="Templates" className="space-y-1">
        <Button variant="outline" size="sm" className="w-full" onClick={() => setSelected('new')}><Plus aria-hidden /> New template</Button>
        {list.length === 0 ? <p className="text-muted-foreground p-2 text-sm">No templates yet.</p> : null}
        {list.map((t) => (
          <button
            key={t.id} type="button" onClick={() => setSelected(t.id)} aria-current={current?.id === t.id}
            className={cn('hover:bg-muted w-full rounded-md px-2 py-1.5 text-left text-sm', current?.id === t.id && selected !== 'new' && 'bg-muted font-medium')}
          >
            <span className="block truncate">{t.name}</span>
            <span className="text-muted-foreground flex gap-1 text-xs">{t.mode ?? 'any mode'}{t.is_starter ? <Badge variant="outline" className="h-4 px-1 text-[10px]">starter</Badge> : null}</span>
          </button>
        ))}
      </nav>
      {selected === 'new' || current ? (
        <TemplateEditor key={editorKey} template={current} onSaved={(id) => setSelected(id)} onDeleted={() => setSelected(null)} />
      ) : (
        <div className="text-muted-foreground rounded-lg border border-dashed p-10 text-center text-sm">Create a template to start.</div>
      )}
    </div>
  )
}

interface EditorProps { template: Template | null; onSaved: (id: number) => void; onDeleted: () => void }

function TemplateEditor({ template, onSaved, onDeleted }: EditorProps) {
  const save = useSaveTemplate()
  const del = useDeleteTemplate()
  const profile = useProfile()
  const initial = useMemo(() => (template ? toIn(template) : BLANK), [template])
  const [form, setForm] = useState<TemplateIn>(initial)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const subjectRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const focus = useRef<'subject' | 'body'>('body')
  useEffect(() => setForm(initial), [initial])

  const dirty = JSON.stringify(form) !== JSON.stringify(initial)
  const chips = [...(profile.data?.reserved ?? ['first_name', 'company', 'role', 'my_name', 'custom_opener']), ...(profile.data?.fields.map((f) => f.key) ?? [])]
  const inUse = used(form)
  const unknown = [...inUse].filter((k) => !chips.includes(k))

  function insert(key: string) {
    const token = `{{${key}}}`
    const field = focus.current
    const el = field === 'subject' ? subjectRef.current : bodyRef.current
    const value = form[field]
    const at = el?.selectionStart ?? value.length
    const end = el?.selectionEnd ?? at
    setForm({ ...form, [field]: value.slice(0, at) + token + value.slice(end) })
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(at + token.length, at + token.length) })
  }
  async function submit() {
    const body: TemplateIn = { ...form, name: form.name.trim(), followup_1_body: form.followup_1_body?.trim() ? form.followup_1_body : null, followup_2_body: form.followup_2_body?.trim() ? form.followup_2_body : null }
    try {
      const t = await save.mutateAsync({ id: template?.id ?? null, body })
      toast.success('Template saved')
      onSaved(t.id)
    } catch { /* save.error shows inline */ }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-2" data-testid="template-editor">
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_160px]">
          <div className="space-y-1"><Label htmlFor="tpl-name">Name</Label><Input id="tpl-name" value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div className="space-y-1">
            <Label htmlFor="tpl-mode">Mode</Label>
            <Select value={form.mode ?? NO_MODE} onValueChange={(v) => setForm({ ...form, mode: v === NO_MODE ? null : (v as Mode) })}>
              <SelectTrigger id="tpl-mode" aria-label="Mode"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_MODE}>Any</SelectItem>
                <SelectItem value="internship">Internship</SelectItem>
                <SelectItem value="investor">Investor</SelectItem>
                <SelectItem value="service">Service</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1"><Label htmlFor="tpl-subject">Subject</Label>
          <Input id="tpl-subject" ref={subjectRef} value={form.subject} maxLength={200} onFocus={() => { focus.current = 'subject' }} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="tpl-body">Body</Label>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Merge fields">
            {chips.map((k) => (
              <Button key={k} type="button" size="xs" variant={inUse.has(k) ? 'secondary' : 'outline'} className="font-mono" onClick={() => insert(k)} aria-label={`Insert ${k}`}>
                {`{{${k}}}`}
              </Button>
            ))}
          </div>
          <Textarea id="tpl-body" ref={bodyRef} rows={12} value={form.body} maxLength={4000} className="font-mono text-sm" onFocus={() => { focus.current = 'body' }} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          <p className="text-muted-foreground text-xs"><code>{'{{custom_opener}}'}</code> is the one or two sentences the local model writes for each lead. Everything else is your text. Plain text only; the opt-out footer is added when sending.</p>
          {unknown.length > 0 ? <p className="text-xs text-amber-600" role="status">Not defined yet: {unknown.map((k) => `{{${k}}}`).join(', ')}. Add {unknown.length === 1 ? 'it' : 'them'} on the Profile tab or drafts will be flagged.</p> : null}
        </div>
        <details className="text-sm">
          <summary className="text-muted-foreground cursor-pointer">Follow-up texts (used in a later step)</summary>
          <div className="mt-2 space-y-2">
            <Textarea aria-label="First follow-up body" rows={4} value={form.followup_1_body ?? ''} onChange={(e) => setForm({ ...form, followup_1_body: e.target.value })} />
            <Textarea aria-label="Second follow-up body" rows={4} value={form.followup_2_body ?? ''} onChange={(e) => setForm({ ...form, followup_2_body: e.target.value })} />
          </div>
        </details>
        {save.error ? <Alert variant="destructive" role="alert"><AlertDescription>{save.error instanceof ApiError ? save.error.detail : 'Could not save.'}</AlertDescription></Alert> : null}
        <div className="flex gap-2">
          <Button onClick={() => void submit()} disabled={save.isPending || !dirty || !form.name.trim() || !form.subject.trim() || !form.body.trim()}>
            {save.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} {template ? 'Save changes' : 'Create template'}
          </Button>
          {dirty && template ? <Button variant="ghost" onClick={() => setForm(initial)}>Discard</Button> : null}
          {template ? <Button variant="ghost" className="ml-auto" onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden /> Delete</Button> : null}
        </div>
      </div>
      <TemplatePreview template={form} />

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader><DialogTitle>Delete this template?</DialogTitle><DialogDescription>A template that a campaign uses cannot be deleted.</DialogDescription></DialogHeader>
          {del.error ? <Alert variant="destructive" role="alert"><AlertDescription>{del.error instanceof ApiError ? del.error.detail : 'Could not delete.'}</AlertDescription></Alert> : null}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button variant="destructive" disabled={del.isPending || template === null}
              onClick={() => { if (template) del.mutate(template.id, { onSuccess: () => { setConfirmDelete(false); onDeleted() } }) }}>
              {del.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
