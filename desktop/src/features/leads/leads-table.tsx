import { flexRender, getCoreRowModel, useReactTable, type ColumnDef, type OnChangeFn, type SortingState, type VisibilityState } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ExternalLink } from 'lucide-react'
import { memo, useEffect, useMemo, useRef } from 'react'
import { ConfidenceBadge, StatusBadge } from '@/components/lead-badges'
import { Checkbox } from '@/components/ui/checkbox'
import type { LeadOut, LeadPatch } from '@/lib/api/types'
import { relativeTime, safeHttpUrl } from '@/lib/format'
import { cn } from '@/lib/utils'
import { EditableCell } from './editable-cell'

export const ROW_HEIGHT = 44
/** A single click opens the drawer after this delay, so a double-click (edit a cell) can cancel it. */
const OPEN_DELAY_MS = 220

/** `min` is the narrowest the column gets; `grow` shares out extra width, so the table fits the window. */
export interface ColumnMeta { min: number; grow: number; label: string; hideable: boolean }

export const COLUMN_META: Record<string, ColumnMeta> = {
  select: { min: 40, grow: 0, label: 'Select', hideable: false },
  name: { min: 140, grow: 1.2, label: 'Name', hideable: false },
  email: { min: 250, grow: 2, label: 'Email', hideable: false },
  role: { min: 110, grow: 1, label: 'Role', hideable: true },
  company: { min: 120, grow: 1, label: 'Company', hideable: true },
  domain: { min: 110, grow: 0.9, label: 'Source', hideable: true },
  status: { min: 84, grow: 0, label: 'Status', hideable: true },
  updated_at: { min: 100, grow: 0, label: 'Last activity', hideable: true },
}

interface Props {
  rows: LeadOut[]
  sorting: SortingState
  onSortingChange: OnChangeFn<SortingState>
  columnVisibility: VisibilityState
  selected: ReadonlySet<number>
  onToggle: (id: number, on: boolean) => void
  onToggleAll: (on: boolean) => void
  onOpen: (id: number) => void
  onEdit: (id: number, patch: LeadPatch) => Promise<void>
}

interface RowProps {
  lead: LeadOut
  index: number
  start: number
  grid: string
  visible: string[]
  isSelected: boolean
  onToggle: Props['onToggle']
  onOpen: Props['onOpen']
  onEdit: Props['onEdit']
}

const Row = memo(function Row({ lead, index, start, grid, visible, isSelected, onToggle, onOpen, onEdit }: RowProps) {
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const cell = (id: string) => {
    switch (id) {
      case 'select':
        return <Checkbox checked={isSelected} aria-label={`Select ${lead.email}`} onClick={(e) => e.stopPropagation()} onCheckedChange={(c) => onToggle(lead.id, c === true)} />
      case 'name':
        return <EditableCell label="Name" value={lead.name ?? ''} className="block truncate text-sm font-medium" onSave={(v) => onEdit(lead.id, { name: v })} />
      case 'email':
        return (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono text-xs" title={lead.email}>{lead.email}</span>
            <ConfidenceBadge value={lead.email_confidence} />
          </div>
        )
      case 'role':
        return <EditableCell label="Role" value={lead.role ?? ''} className="block truncate text-sm" onSave={(v) => onEdit(lead.id, { role: v })} />
      case 'company':
        return <EditableCell label="Company" value={lead.company ?? ''} className="block truncate text-sm" onSave={(v) => onEdit(lead.id, { company: v })} />
      case 'domain': {
        const url = safeHttpUrl(lead.source_url)
        return url ? (
          <a href={url.href} target="_blank" rel="noreferrer noopener" onClick={(e) => e.stopPropagation()}
            className="text-muted-foreground hover:text-foreground flex min-w-0 items-center gap-1 text-xs" title={url.href}>
            <span className="truncate">{lead.domain || url.hostname}</span><ExternalLink className="size-3 shrink-0" aria-label="opens in your browser" />
          </a>
        ) : <span className="text-muted-foreground text-xs">imported</span>
      }
      case 'status':
        return <StatusBadge value={lead.status} />
      case 'updated_at':
        return <span className="text-muted-foreground text-xs">{relativeTime(lead.updated_at ?? lead.created_at)}</span>
      default:
        return null
    }
  }
  return (
    <div
      role="row"
      aria-rowindex={index + 2}
      aria-selected={isSelected}
      tabIndex={0}
      data-testid="lead-row"
      className={cn('hover:bg-muted/50 focus-visible:ring-ring absolute top-0 left-0 grid w-full cursor-pointer items-center gap-3 border-b px-3 outline-none focus-visible:ring-2 focus-visible:ring-inset', isSelected && 'bg-primary/5')}
      style={{ height: ROW_HEIGHT, transform: `translateY(${start}px)`, gridTemplateColumns: grid }}
      onClick={(e) => {
        if (e.detail > 1) return // the second click of a double-click
        window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => onOpen(lead.id), OPEN_DELAY_MS)
      }}
      onDoubleClickCapture={() => window.clearTimeout(timer.current)}
      onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) onOpen(lead.id) }}
    >
      {visible.map((id) => <div key={id} role="cell" className="min-w-0">{cell(id)}</div>)}
    </div>
  )
})

export function LeadsTable({ rows, sorting, onSortingChange, columnVisibility, selected, onToggle, onToggleAll, onOpen, onEdit }: Props) {
  const columns = useMemo<ColumnDef<LeadOut>[]>(
    () => Object.entries(COLUMN_META).map(([id, m]) => ({ id, accessorFn: (r: LeadOut) => (r as unknown as Record<string, unknown>)[id], header: m.label, enableSorting: id !== 'select' })),
    [],
  )
  const table = useReactTable({
    data: rows, columns, getCoreRowModel: getCoreRowModel(), getRowId: (r) => String(r.id),
    manualSorting: true, enableMultiSort: true, enableSortingRemoval: true,
    state: { sorting, columnVisibility }, onSortingChange,
  })

  const visible = table.getVisibleLeafColumns().map((c) => c.id)
  const grid = visible.map((id) => { const m = COLUMN_META[id]; return m && m.grow > 0 ? `minmax(${m.min}px, ${m.grow}fr)` : `${m?.min ?? 100}px` }).join(' ')
  const minWidth = visible.reduce((n, id) => n + (COLUMN_META[id]?.min ?? 100) + 12, 24)

  const scroller = useRef<HTMLDivElement>(null)
  const virt = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => ROW_HEIGHT, overscan: 12 })
  const allOn = rows.length > 0 && rows.every((r) => selected.has(r.id))
  const someOn = !allOn && rows.some((r) => selected.has(r.id))

  return (
    <div ref={scroller} className="bg-background relative h-full overflow-auto rounded-lg border" role="table" aria-label="Leads" aria-rowcount={rows.length + 1} data-testid="leads-scroller">
      <div style={{ minWidth }}>
        <div role="row" aria-rowindex={1} className="bg-background text-muted-foreground sticky top-0 z-10 grid items-center gap-3 border-b px-3 text-xs font-medium" style={{ height: 36, gridTemplateColumns: grid }}>
          {table.getHeaderGroups()[0]?.headers.map((h) => {
            if (h.column.id === 'select') {
              return (
                <div key={h.id} role="columnheader">
                  <Checkbox checked={allOn ? true : someOn ? 'indeterminate' : false} aria-label="Select all loaded leads" onCheckedChange={(c) => onToggleAll(c === true)} />
                </div>
              )
            }
            const dir = h.column.getIsSorted()
            const rank = sorting.length > 1 ? sorting.findIndex((s) => s.id === h.column.id) + 1 : 0
            return (
              <div key={h.id} role="columnheader" aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}>
                <button type="button" className="hover:text-foreground flex items-center gap-1 outline-none focus-visible:underline" onClick={h.column.getToggleSortingHandler()}
                  title="Click to sort. Shift-click to add a second sort.">
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {dir === 'asc' ? <ArrowUp className="size-3" aria-hidden /> : dir === 'desc' ? <ArrowDown className="size-3" aria-hidden /> : null}
                  {rank > 0 ? <sup className="text-[9px]">{rank}</sup> : null}
                </button>
              </div>
            )
          })}
        </div>
        <div className="relative" style={{ height: virt.getTotalSize() }} role="rowgroup">
          {virt.getVirtualItems().map((v) => {
            const lead = rows[v.index]
            if (!lead) return null
            return <Row key={lead.id} lead={lead} index={v.index} start={v.start} grid={grid} visible={visible} isSelected={selected.has(lead.id)} onToggle={onToggle} onOpen={onOpen} onEdit={onEdit} />
          })}
        </div>
      </div>
    </div>
  )
}
