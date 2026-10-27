import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { useLiveRuns } from '@/lib/runs-store'
import { mockApi, renderWithProviders } from '@/test/utils'
import { stubViewport } from '@/test/viewport'
import { LeadsPage } from './leads-page'

beforeAll(() => stubViewport())
beforeEach(() => { localStorage.clear(); useLiveRuns.setState({ runs: {}, leadTick: 0 }) })

const lead = (i: number, over: Record<string, unknown> = {}) => ({
  id: i, name: `Person ${i}`, email: `p${i}@acme.ai`, role: 'CTO', company: `Co${i}`, domain: 'acme.ai', source_url: 'https://acme.ai/team',
  email_confidence: 'published_personal', mode: 'internship', status: 'new', notes: null, created_at: '2026-10-26 10:00:00',
  updated_at: '2026-10-26 10:00:00', search_run_id: 1, ...over,
})
const page = (n: number, total = n) => ({ items: Array.from({ length: n }, (_, i) => lead(i + 1)), total })
const RUNS = [{ id: 1, request_text: 'AI interns', mode: 'internship', state: 'succeeded', stage: 'done', stats: {}, started_at: 'x', finished_at: 'y', pages_fetched: 0, leads_found: 3, error: null, job_id: 1, plan: {} }]

const lastLeadsQuery = (calls: { method: string; path: string }[]) => {
  const c = [...calls].reverse().find((x) => x.method === 'GET' && x.path.startsWith('/leads?'))
  return new URL('http://x' + (c?.path ?? '')).searchParams
}

describe('LeadsPage', () => {
  it('loads leads and shows the count', async () => {
    mockApi({ 'GET /leads': { json: page(3) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads')
    expect(await screen.findAllByTestId('lead-row')).toHaveLength(3)
    expect(screen.getByTestId('lead-count')).toHaveTextContent('3 leads')
  })

  it('reads filters from the URL and sends them to the API', async () => {
    const { calls } = mockApi({ 'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads?q=acme&status=new,sent&conf=guessed&mode=investor&run=1&sort=company:asc,name:desc')
    await screen.findAllByTestId('lead-row')
    const q = lastLeadsQuery(calls)
    expect(q.get('q')).toBe('acme')
    expect(q.getAll('status')).toEqual(['new', 'sent'])
    expect(q.getAll('confidence')).toEqual(['guessed'])
    expect(q.getAll('mode')).toEqual(['investor'])
    expect(q.get('run_id')).toBe('1')
    expect(q.get('sort')).toBe('company:asc,name:desc')
    expect(q.get('limit')).toBe('5000')
    expect(screen.getByTestId('lead-count')).toHaveTextContent('1 matching')
    expect(screen.getByLabelText('Search leads')).toHaveValue('acme')
  })

  it('debounces typing in the search box into one filtered request', async () => {
    const { calls } = mockApi({ 'GET /leads': { json: page(2) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    const before = calls.filter((c) => c.path.startsWith('/leads?')).length
    await userEvent.type(screen.getByLabelText('Search leads'), 'priya')
    await waitFor(() => expect(lastLeadsQuery(calls).get('q')).toBe('priya'))
    const after = calls.filter((c) => c.path.startsWith('/leads?')).length
    expect(after - before).toBeLessThanOrEqual(2) // not one request per keystroke
  })

  it('filters by email type from the menu', async () => {
    const { calls } = mockApi({ 'GET /leads': { json: page(2) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    await userEvent.click(screen.getByRole('button', { name: 'Filter by email type' }))
    await userEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'Guessed' }))
    await waitFor(() => expect(lastLeadsQuery(calls).getAll('confidence')).toEqual(['guessed']))
    await userEvent.keyboard('{Escape}') // an open Radix menu hides the rest of the page from assistive tech
    expect(await screen.findByRole('button', { name: 'Clear filters' })).toBeInTheDocument()
  })

  it('distinguishes "no leads yet" from "no matches"', async () => {
    mockApi({ 'GET /leads': { json: { items: [], total: 0 } }, 'GET /discovery/runs': { json: [] } })
    const { unmount } = renderWithProviders(<LeadsPage />, '/leads')
    expect(await screen.findByText('No leads yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Find leads' })).toHaveAttribute('href', '/discover')
    unmount()
    mockApi({ 'GET /leads': { json: { items: [], total: 0 } }, 'GET /discovery/runs': { json: [] } })
    renderWithProviders(<LeadsPage />, '/leads?q=zzz')
    expect(await screen.findByText('No leads match these filters.')).toBeInTheDocument()
  })

  it('shows an error with a retry instead of an empty table', async () => {
    mockApi({ 'GET /leads': { status: 500, json: { detail: 'database is locked' } }, 'GET /discovery/runs': { json: [] } })
    renderWithProviders(<LeadsPage />, '/leads')
    expect(await screen.findByRole('alert')).toHaveTextContent('database is locked')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('warns when more leads exist than are shown', async () => {
    mockApi({ 'GET /leads': { json: page(2, 8000) }, 'GET /discovery/runs': { json: [] } })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    expect(screen.getByTestId('lead-count')).toHaveTextContent('showing the first 5,000')
  })

  it('refreshes when new leads stream in', async () => {
    const { calls } = mockApi({ 'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    const n = calls.filter((c) => c.path.startsWith('/leads?')).length
    act(() => { useLiveRuns.getState().bumpLeads(); useLiveRuns.getState().bumpLeads() })
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/leads?')).length).toBeGreaterThan(n), { timeout: 3000 })
  })

  it('selecting rows shows the bulk bar; actions call the API with the selected ids', async () => {
    const { calls } = mockApi({
      'GET /leads': { json: page(3) }, 'GET /discovery/runs': { json: RUNS },
      'POST /leads/bulk': { json: { affected: 2, skipped: 0 } },
    })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    expect(screen.queryByTestId('bulk-bar')).toBeNull()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select p1@acme.ai' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select p2@acme.ai' }))
    const bar = screen.getByTestId('bulk-bar')
    expect(bar).toHaveTextContent('2 selected')
    expect(within(bar).getByRole('button', { name: 'Move to campaign' })).toBeDisabled()

    await userEvent.click(within(bar).getByRole('button', { name: /Add to suppression/ }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Add to suppression' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/leads/bulk')).toBe(true))
    expect(calls.find((c) => c.path === '/leads/bulk')?.body).toEqual({ action: 'suppress', ids: [1, 2] })
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull())
  })

  it('delete needs a confirmation and reports leads that were kept', async () => {
    const { calls } = mockApi({
      'GET /leads': { json: page(2) }, 'GET /discovery/runs': { json: RUNS }, 'POST /leads/bulk': { json: { affected: 1, skipped: 1 } },
    })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all loaded leads' }))
    await userEvent.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: /Delete/ }))
    expect(calls.some((c) => c.path === '/leads/bulk')).toBe(false) // nothing happens until confirmed
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/leads/bulk')?.body).toEqual({ action: 'delete', ids: [1, 2] }))
  })

  it('"select all matching" sends the filter, not thousands of ids', async () => {
    const { calls } = mockApi({
      'GET /leads': { json: page(2, 400) }, 'GET /discovery/runs': { json: RUNS }, 'POST /leads/bulk': { json: { affected: 400, skipped: 0 } },
    })
    renderWithProviders(<LeadsPage />, '/leads?conf=guessed')
    await screen.findAllByTestId('lead-row')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all loaded leads' }))
    await userEvent.click(screen.getByRole('button', { name: 'Select all 400 matching' }))
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('400 selected')
    await userEvent.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Set status' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Closed' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/leads/bulk')).toBe(true))
    expect(calls.find((c) => c.path === '/leads/bulk')?.body).toEqual({
      action: 'set_status', status: 'closed', filter: { q: '', status: [], mode: [], confidence: ['guessed'], run_id: null },
    })
  })

  it('opens the details drawer for a row', async () => {
    mockApi({
      'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS },
      'GET /leads/1': { json: { ...lead(1), context_snippet: 'Person 1, CTO', run: null, timeline: [] } },
    })
    renderWithProviders(<LeadsPage />, '/leads')
    await userEvent.click(await screen.findByText('p1@acme.ai'))
    expect(await screen.findByTestId('lead-drawer')).toBeInTheDocument()
  })

  it('remembers which columns are hidden', async () => {
    mockApi({ 'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS } })
    const { unmount } = renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    await userEvent.click(screen.getByRole('button', { name: 'Show or hide columns' }))
    await userEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'Role' }))
    expect(screen.queryByRole('columnheader', { name: /Role/ })).toBeNull()
    expect(JSON.parse(localStorage.getItem('reachout.leads.columns') ?? '{}')).toEqual({ role: false })
    unmount()
    mockApi({ 'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS } })
    renderWithProviders(<LeadsPage />, '/leads')
    await screen.findAllByTestId('lead-row')
    expect(screen.queryByRole('columnheader', { name: /Role/ })).toBeNull()
  })

  it('exports with the current filters', async () => {
    const { calls } = mockApi({
      'GET /leads': { json: page(1) }, 'GET /discovery/runs': { json: RUNS },
      'GET /leads/export.csv': { json: {} },
    })
    URL.createObjectURL = () => 'blob:x'
    URL.revokeObjectURL = () => {}
    renderWithProviders(<LeadsPage />, '/leads?conf=guessed&sort=name:asc')
    await userEvent.click(await screen.findByRole('button', { name: /Export filtered/ }))
    await waitFor(() => expect(calls.some((c) => c.path.startsWith('/leads/export.csv'))).toBe(true))
    const q = new URL('http://x' + (calls.find((c) => c.path.startsWith('/leads/export.csv'))?.path ?? '')).searchParams
    expect(q.getAll('confidence')).toEqual(['guessed'])
    expect(q.get('sort')).toBe('name:asc')
  })
})
