import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useCallback, useState } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { LeadOut, LeadPatch } from '@/lib/api/types'
import { stubViewport } from '@/test/viewport'
import { LeadsTable } from './leads-table'

const renders = vi.hoisted(() => new Map<string, number>())

// Counts renders per row (the name cell's value is unique per lead).
vi.mock('./editable-cell', () => ({
  EditableCell: ({ label, value }: { label: string; value: string }) => {
    if (label === 'Name') renders.set(value, (renders.get(value) ?? 0) + 1)
    return <span>{value}</span>
  },
}))

beforeAll(() => stubViewport())
beforeEach(() => renders.clear())

const lead = (i: number): LeadOut => ({
  id: i, name: `Person ${i}`, email: `p${i}@acme.ai`, role: 'CTO', company: `Co ${i}`, domain: 'acme.ai', source_url: 'https://acme.ai/team',
  email_confidence: 'published_personal', mode: 'internship', status: 'new', notes: null, created_at: '2026-10-26 10:00:00',
  updated_at: '2026-10-26 10:00:00', search_run_id: 1,
})

const ROWS = Array.from({ length: 5000 }, (_, i) => lead(i + 1))

function Harness() {
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const onToggle = useCallback((id: number, on: boolean) => {
    setSelected((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n })
  }, [])
  const onToggleAll = useCallback(() => undefined, [])
  const onOpen = useCallback(() => undefined, [])
  const onEdit = useCallback((_id: number, _p: LeadPatch) => Promise.resolve(), [])
  return (
    <TooltipProvider>
      <LeadsTable rows={ROWS} sorting={[]} onSortingChange={() => undefined} columnVisibility={{}} selected={selected}
        onToggle={onToggle} onToggleAll={onToggleAll} onOpen={onOpen} onEdit={onEdit} />
    </TooltipProvider>
  )
}

describe('LeadsTable at 5,000 leads', () => {
  it('keeps the DOM small and re-renders only the toggled row on selection', async () => {
    render(<Harness />)
    const domRows = screen.getAllByTestId('lead-row').length
    console.info(`[perf] 5000 leads -> ${domRows} DOM rows`)
    expect(domRows).toBeLessThan(100)
    expect(renders.size).toBe(domRows)

    renders.clear()
    await act(async () => { await userEvent.click(screen.getByRole('checkbox', { name: 'Select p3@acme.ai' })) })
    expect(screen.getByRole('checkbox', { name: 'Select p3@acme.ai' })).toBeChecked()
    console.info(`[perf] selection toggle re-rendered ${renders.size} row(s)`)
    expect([...renders.keys()]).toEqual(['Person 3'])
    expect(renders.get('Person 3')).toBe(1)
  })
})
