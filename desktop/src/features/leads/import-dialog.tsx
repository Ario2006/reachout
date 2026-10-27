import { FileUp, Loader2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ApiError } from '@/lib/api/client'
import { useImportCsv } from '@/lib/api/hooks'
import type { ImportReport } from '@/lib/api/types'
import { pluralize } from '@/lib/format'

/** File.text() where available, FileReader otherwise (older engines and jsdom). */
function readText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text()
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(typeof r.result === 'string' ? r.result : '')
    r.onerror = () => reject(r.error ?? new Error('Could not read the file'))
    r.readAsText(file)
  })
}

const COLUMNS = 'name, email, role, company, domain, source_url, email_confidence, mode, notes'

export function ImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const imp = useImportCsv()
  const [text, setText] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [report, setReport] = useState<ImportReport | null>(null)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)

  function reset() { setText(null); setFileName(''); setReport(null); setError(''); if (input.current) input.current.value = '' }
  function close(o: boolean) { if (!o) reset(); onOpenChange(o) }

  async function pick(file: File | undefined) {
    if (!file) return
    reset()
    setFileName(file.name)
    let content: string
    try {
      content = await readText(file)
    } catch {
      setError('Could not read that file')
      return
    }
    setText(content)
    imp.mutate({ text: content, dryRun: true }, {
      onSuccess: setReport,
      onError: (e) => setError(e instanceof ApiError ? e.detail : 'Could not read the file'),
    })
  }

  function commit() {
    if (text === null) return
    imp.mutate({ text, dryRun: false }, {
      onSuccess: (r) => { toast.success(`Imported ${pluralize(r.inserted, 'lead')}`); close(false) },
      onError: (e) => setError(e instanceof ApiError ? e.detail : 'Import failed'),
    })
  }

  const previewing = imp.isPending && imp.variables?.dryRun === true
  const importing = imp.isPending && imp.variables?.dryRun === false

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Import leads from CSV</DialogTitle>
          <DialogDescription>
            Columns: <span className="font-mono text-xs">{COLUMNS}</span>. Only <span className="font-mono text-xs">email</span> is required.
            Addresses are checked (syntax, mail server, suppression list) before anything is saved.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" id="csv-file" onChange={(e) => void pick(e.target.files?.[0])} />
          <Button variant="outline" onClick={() => input.current?.click()} disabled={imp.isPending}>
            <FileUp aria-hidden /> {fileName || 'Choose a CSV file'}
          </Button>
          {previewing ? <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status"><Loader2 className="size-4 animate-spin" aria-hidden /> Checking addresses…</p> : null}
          {error ? <p className="text-destructive text-sm" role="alert">{error}</p> : null}

          {report ? (
            <div className="space-y-2" data-testid="import-report">
              <p className="text-sm">
                <strong>{pluralize(report.valid, 'lead')}</strong> ready to import from {pluralize(report.total, 'row')}.
              </p>
              <ul className="text-muted-foreground grid grid-cols-2 gap-x-4 text-xs">
                <li>{report.invalid} invalid address(es)</li>
                <li>{report.no_mx} with no mail server</li>
                <li>{report.duplicates} duplicate(s)</li>
                <li>{report.suppressed} on the suppression list</li>
              </ul>
              {report.issues.length > 0 ? (
                <details className="text-xs">
                  <summary className="text-muted-foreground cursor-pointer">Skipped rows ({report.issues.length}{report.issues.length >= 100 ? '+' : ''})</summary>
                  <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto font-mono">
                    {report.issues.map((i) => <li key={`${i.row}-${i.reason}`}>row {i.row}: {i.email || '(blank)'} — {i.reason}</li>)}
                  </ul>
                </details>
              ) : null}
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
          <Button onClick={commit} disabled={!report || report.valid === 0 || imp.isPending}>
            {importing ? <Loader2 className="animate-spin" aria-hidden /> : null} Import {report ? pluralize(report.valid, 'lead') : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
