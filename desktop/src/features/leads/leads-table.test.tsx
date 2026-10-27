import type { SortingState } from '@tanstack/react-table'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { LeadOut, LeadPatch } from '@/lib/api/types'
import { stubViewport } from '@/test/viewport'
import { LeadsTable } from './leads-table'

beforeAll(() => stubViewport())

const lead = (i: number, over: Partial<LeadOut> = {}): LeadOut => ({
  id: i, name: `Person ${i}`, email: `p${i}@acme.ai`, role: 'CTO', company: `Co ${i}`, domain: 'acme.ai', source_url: 'https://acme.ai/team',
  email_confidence: 'published_personal', mode: 'internship', status: 'new', notes: null, created_at: '2026-10-26 10:00:00',
  updated_at: '2026-10-26 10:00:00', search_run_id: 1, ...over,
})

function setup(rows: LeadOut[], over: Partial<Parameters<typeof LeadsTable>[0]> = {}) {
  const spies = {
    onSortingChange: vi.fn<(u: SortingState | ((old: SortingState) => SortingState)) => void>(),
    onToggle: vi.fn<(id: number, on: boolean) => void>(),
    onToggleAll: vi.fn<(on: boolean) => void>(),
    onOpen: vi.fn<(id: number) => void>(),
    onEdit: vi.fn<(id: number, patch: LeadPatch) => Promise<void>>().mockResolvedValue(undefined),
  }
  render(
    <TooltipProvider>
      <LeadsTable rows={rows} sorting={[]} columnVisibility={{}} selected={new Set<number>()} {...spies} {...over} />
    </TooltipProvider>,
  )
  return spies
}

describe('LeadsTable', () => {
  it('virtualises: 5,000 leads render only a window of rows', () => {
    setup(Array.from({ length: 5000 }, (_, i) => lead(i + 1)))
    const rendered = screen.getAllByTestId('lead-row').length
    expect(rendered).toBeGreaterThan(5)
    expect(rendered).toBeLessThan(60)
    expect(screen.getByRole('table', { name: 'Leads' })).toHaveAttribute('aria-rowcount', '5001')
  })

  it('shows name, email with its confidence badge, role, company and source', () => {
    setup([lead(1, { email_confidence: 'guessed' })])
    const row = screen.getByTestId('lead-row')
    expect(within(row).getByText('Person 1')).toBeInTheDocument()
    expect(within(row).getByText('p1@acme.ai')).toBeInTheDocument()
    expect(within(row).getByTestId('conf-guessed')).toHaveTextContent('Guessed')
    expect(within(row).getByText('acme.ai')).toBeInTheDocument()
    expect(within(row).getByRole('link')).toHaveAttribute('target', '_blank')
    expect(within(row).getByRole('link')).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it.each(['n/a', 'javascript:alert(1)', 'file:///etc/passwd', '::::', 'x'])('never links or crashes on an odd source_url (%s)', (bad) => {
    setup([lead(1, { source_url: bad, domain: 'x.com' })])
    expect(screen.getByTestId('lead-row')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('imported')).toBeInTheDocument()
  })

  it('labels imported leads that have no source page', () => {
    setup([lead(1, { source_url: '', domain: 'x.com' })])
    expect(screen.getByText('imported')).toBeInTheDocument()
  })

  it('click sorts and shift-click adds a second sort key', async () => {
    const props = setup([lead(1)])
    const user = userEvent.setup() // one instance, so a held Shift key persists between calls
    const header = (name: RegExp) => within(screen.getByRole('columnheader', { name })).getByRole('button')
    await user.click(header(/Company/))
    expect(props.onSortingChange).toHaveBeenCalledTimes(1)
    const updater = props.onSortingChange.mock.calls[0]?.[0] as (s: SortingState) => SortingState
    expect(updater([])).toEqual([{ id: 'company', desc: false }])
    // shift-click appends to the existing sort instead of replacing it
    await user.keyboard('{Shift>}')
    await user.click(header(/Role/))
    await user.keyboard('{/Shift}')
    const multi = props.onSortingChange.mock.calls[1]?.[0] as (s: SortingState) => SortingState
    expect(multi([{ id: 'company', desc: false }]).map((s) => s.id)).toEqual(['company', 'role'])
  })

  it('exposes sort state to assistive tech and numbers a multi-sort', () => {
    setup([lead(1)], { sorting: [{ id: 'company', desc: false }, { id: 'name', desc: true }] })
    expect(screen.getByRole('columnheader', { name: /Company/ })).toHaveAttribute('aria-sort', 'ascending')
    expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute('aria-sort', 'descending')
    expect(screen.getByRole('columnheader', { name: /Role/ })).toHaveAttribute('aria-sort', 'none')
  })

  it('selection checkboxes report toggles and select-all shows a mixed state', async () => {
    const props = setup([lead(1), lead(2)], { selected: new Set([1]) })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select p2@acme.ai' }))
    expect(props.onToggle).toHaveBeenCalledWith(2, true)
    const all = screen.getByRole('checkbox', { name: 'Select all loaded leads' })
    expect(all).toHaveAttribute('aria-checked', 'mixed')
    await userEvent.click(all)
    expect(props.onToggleAll).toHaveBeenCalled()
    expect(screen.getAllByTestId('lead-row')[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('a click anywhere on a row, including the name cell, opens the lead; Enter opens it at once', async () => {
    const props = setup([lead(1)])
    const row = screen.getByTestId('lead-row')
    await userEvent.click(within(row).getByText('p1@acme.ai'))
    await waitFor(() => expect(props.onOpen).toHaveBeenCalledWith(1))
    props.onOpen.mockClear()
    await userEvent.click(within(row).getByRole('button', { name: /^Name:/ })) // the most natural thing to click
    await waitFor(() => expect(props.onOpen).toHaveBeenCalledWith(1))
    props.onOpen.mockClear()
    row.focus()
    await userEvent.keyboard('{Enter}')
    expect(props.onOpen).toHaveBeenCalledWith(1)
  })

  it('the checkbox and the source link never open the drawer', async () => {
    const props = setup([lead(1)])
    const row = screen.getByTestId('lead-row')
    await userEvent.click(within(row).getByRole('checkbox'))
    await userEvent.click(within(row).getByRole('link'))
    await new Promise((r) => setTimeout(r, 350))
    expect(props.onOpen).not.toHaveBeenCalled()
  })

  it('double-clicking a cell edits it and does not open the drawer', async () => {
    const props = setup([lead(1)])
    await userEvent.dblClick(screen.getByRole('button', { name: /^Company: Co 1/ }))
    await userEvent.clear(screen.getByRole('textbox', { name: 'Edit Company' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Edit Company' }), 'Renamed{Enter}')
    expect(props.onEdit).toHaveBeenCalledWith(1, { company: 'Renamed' })
    await new Promise((r) => setTimeout(r, 350))
    expect(props.onOpen).not.toHaveBeenCalled()
  })

  it('hides columns that are switched off', () => {
    setup([lead(1)], { columnVisibility: { role: false, company: false, updated_at: false } })
    expect(screen.queryByRole('columnheader', { name: /Role/ })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: /Company/ })).toBeNull()
    expect(screen.getByRole('columnheader', { name: /Email/ })).toBeInTheDocument()
  })
})
