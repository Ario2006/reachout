import { useEffect, useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { usePreviewLeads, useTemplatePreview } from '@/lib/api/hooks-compose'
import type { TemplateIn } from '@/lib/api/types'
import { HighlightedText } from './highlighted-text'

const SAMPLE = 'sample'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export function TemplatePreview({ template }: { template: TemplateIn }) {
  const [lead, setLead] = useState(SAMPLE)
  const [asSent, setAsSent] = useState(false)
  const leads = usePreviewLeads()
  const debounced = useDebounced(template, 400)
  const preview = useTemplatePreview(debounced, lead === SAMPLE ? null : Number(lead))
  const p = preview.data

  return (
    <section className="space-y-3" aria-label="Preview" data-testid="template-preview">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <h3 className="text-sm font-medium">Preview</h3>
        <div className="flex items-end gap-2">
          <Label htmlFor="preview-lead" className="text-xs">Lead</Label>
          <Select value={lead} onValueChange={setLead}>
            <SelectTrigger id="preview-lead" className="w-52" size="sm" aria-label="Preview against lead"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={SAMPLE}>Sample lead</SelectItem>
              {(leads.data?.items ?? []).map((l) => <SelectItem key={l.id} value={String(l.id)}>{l.name ?? l.email}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      {preview.isError ? (
        <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-3">
          {preview.error instanceof ApiError ? preview.error.detail : 'The preview failed.'}
          <Button size="xs" variant="outline" onClick={() => void preview.refetch()}>Retry</Button>
        </AlertDescription></Alert>
      ) : null}
      {!p && preview.isPending ? <div className="space-y-2" role="status" aria-label="Loading preview"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-32" /></div> : null}
      {p ? (
        <div className="space-y-2">
          <p className="text-sm"><span className="text-muted-foreground">Subject: </span><span className="font-medium" data-testid="preview-subject">{p.subject}</span></p>
          <div className="bg-card rounded-lg border p-3">
            <HighlightedText text={asSent ? p.final_text : p.body} span={p.opener_span} />
          </div>
          <div className="text-muted-foreground flex flex-wrap items-center gap-3 text-xs">
            <span><mark className="bg-primary/15 rounded-sm px-1">Highlighted</mark> text is written by the local model for each lead. This is a placeholder.</span>
            <span data-testid="word-count" className={p.word_count > 120 ? 'text-amber-700 dark:text-amber-400' : ''}>{p.word_count} words{p.word_count > 120 ? ' (aim for under 120)' : ''}</span>
            <Button variant="link" size="xs" onClick={() => setAsSent((v) => !v)}>{asSent ? 'Show email body' : 'Show as sent (with footer)'}</Button>
          </div>
          {p.missing_fields.length > 0 ? (
            <Alert data-testid="missing-fields"><AlertDescription>Empty for this lead: {p.missing_fields.map((f) => `{{${f}}}`).join(', ')}. Drafts with empty fields are flagged. Fill profile fields on the Profile tab.</AlertDescription></Alert>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
