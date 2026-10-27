import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderWithProviders } from '@/test/utils'
import { LeadDrawer } from './lead-drawer'

const detail = (over: Record<string, unknown> = {}) => ({
  id: 4, name: 'Priya Sharma', email: 'priya@acme.ai', role: 'CEO', company: 'Acme AI', domain: 'acme.ai', source_url: 'https://acme.ai/team',
  email_confidence: 'published_personal', mode: 'internship', status: 'new', notes: null, created_at: '2026-10-26 10:00:00', updated_at: '2026-10-26 10:00:00',
  search_run_id: 2, context_snippet: 'Priya Sharma, Co-founder and CEO', run: { id: 2, request_text: 'AI interns in Bangalore', started_at: 'x' }, timeline: [], score: 81, score_reasons: [{ label: 'confidence', points: 40, detail: "A person's address published on the site" }, { label: 'name', points: 15, detail: 'The address matches the name Priya Sharma' }],
  sources: [{ source_url: 'https://acme.ai/team', page_type: 'team', page_tier: 'http' }], trace: [{ at: 'x', kind: 'found', message: 'Priya Sharma is on the team page' }, { at: 'x', kind: 'score', message: 'Scored 81/100' }], ...over,
})

describe('LeadDrawer', () => {
  it('shows where the lead came from, with the real page text', async () => {
    mockApi({ 'GET /leads/4': { json: detail() } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => {}} />)
    expect(await screen.findByTestId('lead-drawer')).toBeInTheDocument()
    expect(screen.getByText('priya@acme.ai')).toBeInTheDocument()
    expect(screen.getByText('“Priya Sharma, Co-founder and CEO”')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /acme\.ai\/team/ })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: 'AI interns in Bangalore' })).toHaveAttribute('href', '/leads?run=2')
    expect(screen.getByText(/Nothing yet/)).toBeInTheDocument()
  })

  it('renders the timeline when there is activity', async () => {
    mockApi({ 'GET /leads/4': { json: detail({ timeline: [{ type: 'sent', detail: 'initial', at: '2026-10-26 11:00:00' }, { type: 'reply', detail: 'Sounds good', at: '2026-10-26 12:00:00' }] }) } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => {}} />)
    expect(await screen.findByText('Email sent')).toBeInTheDocument()
    expect(screen.getByText('Replied')).toBeInTheDocument()
    expect(screen.getByText('Sounds good')).toBeInTheDocument()
  })

  it('explains a guessed address and imported leads without a source', async () => {
    mockApi({ 'GET /leads/4': { json: detail({ email_confidence: 'guessed', source_url: '', run: null, search_run_id: null, notes: 'Guessed (first@) from a listed person; not verified' }) } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => {}} />)
    expect(await screen.findByText(/Guessed \(first@\) from a listed person/, { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText(/Imported from a file/)).toBeInTheDocument()
    expect(screen.getByTestId('conf-guessed')).toBeInTheDocument()
  })

  it('saves only through the API, with the edited fields', async () => {
    const { calls } = mockApi({ 'GET /leads/4': { json: detail() }, 'PATCH /leads/4': { json: detail({ name: 'P. Sharma' }) }, 'GET /leads': { json: { items: [], total: 0 } } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => {}} />)
    const save = await screen.findByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()
    const name = screen.getByDisplayValue('Priya Sharma')
    await userEvent.clear(name)
    await userEvent.type(name, 'P. Sharma')
    expect(save).toBeEnabled()
    await userEvent.type(screen.getByRole('textbox', { name: 'Notes' }), 'met at demo day')
    await userEvent.click(save)
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ name: 'P. Sharma', role: 'CEO', company: 'Acme AI', notes: 'met at demo day', status: 'new' })
  })

  it('shows an error instead of a blank panel when the lead cannot be loaded', async () => {
    mockApi({ 'GET /leads/4': { status: 404, json: { detail: 'Lead not found' } } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this lead')
  })

  it('renders nothing for no lead', () => {
    mockApi({})
    renderWithProviders(<LeadDrawer id={null} onClose={() => {}} />)
    expect(screen.queryByTestId('lead-drawer')).toBeNull()
  })

  it('shows the score with its reasons, every source page, and the trace behind the lead', async () => {
    mockApi({ 'GET /leads/4': { json: detail({ sources: [
      { source_url: 'https://acme.ai/team', page_type: 'team', page_tier: 'http' },
      { source_url: 'https://acme.ai/about', page_type: 'about', page_tier: 'cache' },
    ] }) } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => undefined} />)
    expect(await screen.findByLabelText('Score 81 out of 100')).toBeInTheDocument()
    expect(screen.getByText('+40')).toBeInTheDocument()
    expect(screen.getByText('The address matches the name Priya Sharma')).toBeInTheDocument()
    expect(screen.getByText('Seen on 2 pages')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Why was this found?'))
    expect(screen.getByText('Priya Sharma is on the team page')).toBeVisible()
  })

  it('shows no score section for imported leads', async () => {
    mockApi({ 'GET /leads/4': { json: detail({ score: null, score_reasons: [], sources: [], trace: [] }) } })
    renderWithProviders(<LeadDrawer id={4} onClose={() => undefined} />)
    await screen.findByText('Where this came from')
    expect(screen.queryByText('Why was this found?')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^Score /)).not.toBeInTheDocument()
  })
})
