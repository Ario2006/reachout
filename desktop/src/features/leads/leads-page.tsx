import { useQueryClient } from '@tanstack/react-query'
import type { OnChangeFn, SortingState, VisibilityState } from '@tanstack/react-table'
import { Columns3, Download, FileUp, Search, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { CONFIDENCE_OPTIONS, MODE_OPTIONS, STATUS_OPTIONS } from '@/components/lead-badges'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { downloadLeadsCsv, LEADS_LIMIT, usePatchLead, useRuns, useLeads, type LeadQuery } from '@/lib/api/hooks'
import type { LeadPatch } from '@/lib/api/types'
import { pluralize } from '@/lib/format'
import { useLiveRuns } from '@/lib/runs-store'
import { BulkBar } from './bulk-bar'
import { FilterMenu } from './filter-menu'
import { ImportDialog } from './import-dialog'
import { LeadDrawer } from './lead-drawer'
import { LeadsTable, COLUMN_META } from './leads-table'
import { formatSort, hasFilters, paramsFromQuery, parseSort, queryFromParams } from './query-params'

const COLS_KEY = 'reachout.leads.columns'

function loadVisibility(): VisibilityState {
  try {
    const raw = localStorage.getItem(COLS_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    return typeof parsed === 'object' && parsed !== null ? (parsed as VisibilityState) : {}
  } catch {
    return {}
  }
}

export function LeadsPage() {
  const qc = useQueryClient()
  const [sp, setSp] = useSearchParams()
  const query = useMemo(() => queryFromParams(sp), [sp])
  const setQuery = useCallback((patch: Partial<LeadQuery>) => setSp(paramsFromQuery({ ...query, ...patch }), { replace: true }), [query, setSp])

  const [search, setSearch] = useState(query.q)
  useEffect(() => { setSearch(query.q) }, [query.q])
  useEffect(() => {
    if (search === query.q) return
    const t = setTimeout(() => setQuery({ q: search }), 250)
    return () => clearTimeout(t)
  }, [search, query.q, setQuery])

  const leads = useLeads(query)
  const runs = useRuns()
  const patch = usePatchLead()
  const rows = leads.data?.items ?? []
  const total = leads.data?.total ?? 0

  // Leads stream in during a search: refresh (debounced) whenever the backend reports new ones.
  const tick = useLiveRuns((s) => s.leadTick)
  useEffect(() => {
    if (tick === 0) return
    const t = setTimeout(() => void qc.invalidateQueries({ queryKey: ['leads'] }), 600)
    return () => clearTimeout(t)
  }, [tick, qc])

  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [allMatching, setAllMatching] = useState(false)
  const filterKey = JSON.stringify([query.q, query.status, query.mode, query.confidence, query.run_id, query.min_score])
  useEffect(() => { setSelected(new Set()); setAllMatching(false) }, [filterKey])

  const [openId, setOpenId] = useState<number | null>(null)
  const [importing, setImporting] = useState(false)
  const [visibility, setVisibility] = useState<VisibilityState>(loadVisibility)
  const sorting = useMemo(() => parseSort(query.sort), [query.sort])

  const onSortingChange: OnChangeFn<SortingState> = (updater) => {
    const next = typeof updater === 'function' ? updater(sorting) : updater
    setQuery({ sort: formatSort(next) })
  }
  const toggleColumn = (id: string, on: boolean) => {
    const next = { ...visibility, [id]: on }
    setVisibility(next)
    try { localStorage.setItem(COLS_KEY, JSON.stringify(next)) } catch { /* ignore */ }
  }
  const onToggle = useCallback((id: number, on: boolean) => {
    setAllMatching(false)
    setSelected((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n })
  }, [])
  const onToggleAll = useCallback((on: boolean) => {
    setAllMatching(false)
    setSelected(on ? new Set(rows.map((r) => r.id)) : new Set())
  }, [rows])
  const patchLead = patch.mutateAsync // stable across renders, so memoised table rows are not invalidated
  const onEdit = useCallback(async (id: number, p: LeadPatch) => {
    try { await patchLead({ id, patch: p }) } catch (e) { toast.error(e instanceof ApiError ? e.detail : 'Could not save') }
  }, [patchLead])

  const filtered = hasFilters(query)
  const run = runs.data?.find((r) => r.id === query.run_id)

  return (
    <div className="flex h-full flex-col gap-3 px-6 pt-2 pb-6">
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Leads</h1>
        <p className="text-muted-foreground text-sm" aria-live="polite" data-testid="lead-count">
          {leads.isPending ? 'Loading…' : filtered ? `${total.toLocaleString()} matching` : pluralize(total, 'lead')}
          {total > LEADS_LIMIT ? ` · showing the first ${LEADS_LIMIT.toLocaleString()}, refine the filters to see the rest` : ''}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Lead filters">
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-2 left-2 size-3.5" aria-hidden />
          <Input aria-label="Search leads" placeholder="Search name, email, company…" className="h-8 w-64 pl-7" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <FilterMenu label="Status" options={STATUS_OPTIONS} selected={query.status} onChange={(v) => setQuery({ status: v })} />
        <FilterMenu label="Mode" options={MODE_OPTIONS} selected={query.mode} onChange={(v) => setQuery({ mode: v })} />
        <FilterMenu label="Email type" options={CONFIDENCE_OPTIONS} selected={query.confidence} onChange={(v) => setQuery({ confidence: v })} />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant={query.min_score !== null ? 'secondary' : 'outline'} size="sm" aria-label="Filter by minimum score">{query.min_score !== null ? `Score ${query.min_score}+` : 'Score'}</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Minimum score</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuRadioGroup value={query.min_score === null ? 'any' : String(query.min_score)} onValueChange={(v) => setQuery({ min_score: v === 'any' ? null : Number(v) })}>
              <DropdownMenuRadioItem value="any">Any score</DropdownMenuRadioItem>
              {[40, 60, 80].map((n) => <DropdownMenuRadioItem key={n} value={String(n)}>{n} or more</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant={query.run_id !== null ? 'secondary' : 'outline'} size="sm" aria-label="Filter by search run">{run ? `Search #${run.id}` : query.run_id !== null ? `Search #${query.run_id}` : 'Search run'}</Button></DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-w-80">
            <DropdownMenuLabel>Leads from</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuRadioGroup value={query.run_id === null ? 'all' : String(query.run_id)} onValueChange={(v) => setQuery({ run_id: v === 'all' ? null : Number(v) })}>
              <DropdownMenuRadioItem value="all">All searches and imports</DropdownMenuRadioItem>
              {(runs.data ?? []).map((r) => <DropdownMenuRadioItem key={r.id} value={String(r.id)}><span className="truncate">#{r.id} {r.request_text}</span></DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        {filtered ? <Button variant="ghost" size="sm" onClick={() => { setSearch(''); setSp(new URLSearchParams(), { replace: true }) }}><X aria-hidden /> Clear filters</Button> : null}

        <div className="ml-auto flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="outline" size="sm" aria-label="Show or hide columns"><Columns3 aria-hidden /> Columns</Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {Object.entries(COLUMN_META).filter(([, m]) => m.hideable).map(([id, m]) => (
                <DropdownMenuCheckboxItem key={id} checked={visibility[id] !== false} onSelect={(e) => e.preventDefault()} onCheckedChange={(on) => toggleColumn(id, on)}>{m.label}</DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="sm" onClick={() => setImporting(true)}><FileUp aria-hidden /> Import CSV</Button>
          <Button variant="outline" size="sm" disabled={total === 0}
            onClick={() => void downloadLeadsCsv(query).catch((e: unknown) => toast.error(e instanceof ApiError ? e.detail : 'Export failed'))}>
            <Download aria-hidden /> Export{filtered ? ' filtered' : ''}
          </Button>
        </div>
      </div>

      {selected.size > 0 || allMatching ? (
        <BulkBar count={selected.size} total={total} loaded={rows.length} ids={[...selected]} allMatching={allMatching}
          filter={{ q: query.q, status: query.status, mode: query.mode, confidence: query.confidence, run_id: query.run_id, min_score: query.min_score }}
          onSelectAllMatching={() => setAllMatching(true)} onClear={() => { setSelected(new Set()); setAllMatching(false) }} />
      ) : null}

      <div className="min-h-0 flex-1">
        {leads.isPending ? (
          <div className="space-y-2" role="status" aria-label="Loading leads">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-11" />)}</div>
        ) : leads.isError ? (
          <div className="flex flex-col items-start gap-3 rounded-lg border p-6" role="alert">
            <p className="text-destructive text-sm">{leads.error instanceof ApiError ? leads.error.detail : 'Could not load leads.'}</p>
            <Button variant="outline" onClick={() => void leads.refetch()}>Retry</Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="text-muted-foreground flex h-full min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-8 text-center text-sm" data-testid="leads-empty">
            {filtered ? (
              <>
                <p>No leads match these filters.</p>
                <Button variant="outline" size="sm" onClick={() => { setSearch(''); setSp(new URLSearchParams(), { replace: true }) }}>Clear filters</Button>
              </>
            ) : (
              <>
                <p className="text-foreground font-medium">No leads yet</p>
                <p>Describe who you want to reach in Discover, or import a CSV you already have.</p>
                <div className="flex gap-2"><Button asChild size="sm"><Link to="/discover">Find leads</Link></Button><Button variant="outline" size="sm" onClick={() => setImporting(true)}>Import CSV</Button></div>
              </>
            )}
          </div>
        ) : (
          <LeadsTable rows={rows} sorting={sorting} onSortingChange={onSortingChange} columnVisibility={visibility} selected={selected}
            onToggle={onToggle} onToggleAll={onToggleAll} onOpen={setOpenId} onEdit={onEdit} />
        )}
      </div>

      <LeadDrawer id={openId} onClose={() => setOpenId(null)} />
      <ImportDialog open={importing} onOpenChange={setImporting} />
    </div>
  )
}
