import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { useLiveRuns } from '@/lib/runs-store'
import { mockApi, renderWithProviders } from '@/test/utils'
import { DiscoverPage } from './discover-page'
import { deriveSteps } from './run-progress'

const PLAN = {
  plan: { mode: 'internship', target_roles: ['founder'], industries: ['AI'], regions: ['Bangalore'], keywords: ['seed'], search_queries: ['ai startup bangalore careers', 'top ai startups in bangalore'] },
  source: 'llm', note: '',
}
const run = (over: Record<string, unknown> = {}) => ({
  id: 1, request_text: 'AI interns', mode: 'internship', state: 'succeeded', stage: 'done', stats: {}, started_at: '2026-10-26 10:00:00',
  finished_at: '2026-10-26 10:05:00', pages_fetched: 0, leads_found: 0, error: null, job_id: 1, plan: PLAN.plan, ...over,
})

beforeEach(() => useLiveRuns.setState({ runs: {}, leadTick: 0 }))

describe('deriveSteps', () => {
  const states = (stage: string, state: string, s: Record<string, unknown>) => deriveSteps(stage, state, s).map((x) => x.state)

  it('starts with only searching active', () => {
    expect(states('searching', 'running', { queries_done: 2, queries_total: 8 })).toEqual(['active', 'pending', 'pending', 'pending'])
  })
  it('overlaps crawling, extracting and validating as counters appear', () => {
    expect(states('crawling', 'running', { domains_total: 10 })).toEqual(['done', 'active', 'pending', 'pending'])
    expect(states('crawling', 'running', { domains_total: 10, pages: 3 })).toEqual(['done', 'active', 'active', 'pending'])
    expect(states('crawling', 'running', { domains_total: 10, pages: 3, leads_checked: 2 })).toEqual(['done', 'active', 'active', 'active'])
  })
  it('marks everything done when finished, and never claims work that did not happen', () => {
    expect(states('done', 'succeeded', { pages: 5, leads_checked: 3 })).toEqual(['done', 'done', 'done', 'done'])
    expect(states('done', 'succeeded', { pages: 0, leads_checked: 0 })).toEqual(['done', 'done', 'pending', 'pending'])
    expect(states('crawling', 'cancelled', { pages: 5 })).toEqual(['done', 'done', 'done', 'pending'])
  })
  it('describes each step with its counters', () => {
    const steps = deriveSteps('crawling', 'running', { queries_done: 12, queries_total: 12, search_hits: 94, domains_done: 3, domains_total: 40, pages: 17, emails_found: 9, people_found: 4, leads_new: 5, leads_dropped: 1 })
    expect(steps[0]?.detail).toBe('12/12 queries · 94 results')
    expect(steps[1]?.detail).toBe('3/40 sites · 17 pages')
    expect(steps[2]?.detail).toBe('9 emails · 4 people')
    expect(steps[3]?.detail).toContain('5 kept')
    expect(deriveSteps('done', 'succeeded', { leads_new: 5003, search_hits: 1200 }).map((x) => x.detail).join(' ')).toMatch(/5,003 kept.*|1,200 results/)
  })
})

describe('DiscoverPage', () => {
  it('makes a plan, lets you edit it, and starts the run with exactly that plan', async () => {
    const { calls } = mockApi({
      'GET /discovery/runs': { json: [] },
      'POST /discovery/plan': { json: PLAN },
      'POST /discovery/run': { status: 202, json: run({ state: 'queued', id: 5 }) },
      'GET /discovery/runs/5': { json: { ...run({ state: 'queued', id: 5 }), domains: [], log: [] } },
    })
    renderWithProviders(<DiscoverPage />)
    await userEvent.type(screen.getByLabelText('Who do you want to reach?'), 'AI internships in Bangalore')
    await userEvent.click(screen.getByRole('button', { name: 'Internship' }))
    await userEvent.click(screen.getByRole('button', { name: 'Make a plan' }))

    const card = await screen.findByTestId('plan-card')
    expect(calls.find((c) => c.path === '/discovery/plan')?.body).toEqual({ request: 'AI internships in Bangalore', mode: 'internship' })
    expect(within(card).getByText('Written by your local model')).toBeInTheDocument()

    await userEvent.click(within(card).getByRole('button', { name: 'Remove top ai startups in bangalore' }))
    await userEvent.type(within(card).getByRole('textbox', { name: /Industries/ }), 'fintech{Enter}')
    await userEvent.click(within(card).getByRole('button', { name: 'Investor' }))
    await userEvent.click(within(card).getByRole('checkbox', { name: /Follow list pages/ }))
    await userEvent.click(within(card).getByRole('button', { name: 'Run search' }))

    await waitFor(() => expect(calls.some((c) => c.path === '/discovery/run')).toBe(true))
    const body = calls.find((c) => c.path === '/discovery/run')?.body as { request_text: string; plan: typeof PLAN.plan; options: Record<string, boolean> }
    expect(body.request_text).toBe('AI internships in Bangalore')
    expect(body.plan.mode).toBe('investor')
    expect(body.plan.industries).toEqual(['AI', 'fintech'])
    expect(body.plan.search_queries).toEqual(['ai startup bangalore careers'])
    expect(body.options).toEqual({ recrawl: false, use_overpass: false, expand_lists: false })
    expect(await screen.findByTestId('run-progress')).toBeInTheDocument()
    expect(screen.queryByTestId('plan-card')).toBeNull()
  })

  it('flags a template plan clearly', async () => {
    mockApi({ 'GET /discovery/runs': { json: [] }, 'POST /discovery/plan': { json: { ...PLAN, source: 'fallback', note: 'The language model was not available. Please review it.' } } })
    renderWithProviders(<DiscoverPage />)
    await userEvent.type(screen.getByLabelText('Who do you want to reach?'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Make a plan' }))
    expect(await screen.findByText('Template plan')).toBeInTheDocument()
    expect(screen.getByText(/language model was not available/)).toBeInTheDocument()
  })

  it('offers OpenStreetMap only for service mode', async () => {
    mockApi({ 'GET /discovery/runs': { json: [] }, 'POST /discovery/plan': { json: PLAN } })
    renderWithProviders(<DiscoverPage />)
    await userEvent.type(screen.getByLabelText('Who do you want to reach?'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Make a plan' }))
    const card = await screen.findByTestId('plan-card')
    expect(within(card).queryByRole('checkbox', { name: /OpenStreetMap/ })).toBeNull()
    await userEvent.click(within(card).getByRole('button', { name: 'Service' }))
    expect(within(card).getByRole('checkbox', { name: /OpenStreetMap/ })).toBeInTheDocument()
  })

  it('blocks Run when every query has been removed', async () => {
    mockApi({ 'GET /discovery/runs': { json: [] }, 'POST /discovery/plan': { json: { ...PLAN, plan: { ...PLAN.plan, search_queries: ['only one'] } } } })
    renderWithProviders(<DiscoverPage />)
    await userEvent.type(screen.getByLabelText('Who do you want to reach?'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Make a plan' }))
    const card = await screen.findByTestId('plan-card')
    await userEvent.click(within(card).getByRole('button', { name: 'Remove only one' }))
    expect(within(card).getByRole('button', { name: 'Run search' })).toBeDisabled()
  })

  it('shows an active run and locks the request box while it runs', async () => {
    mockApi({
      'GET /discovery/runs': { json: [run({ state: 'running', stage: 'crawling' })] },
      'GET /discovery/runs/1': { json: { ...run({ state: 'running', stage: 'crawling', stats: { domains_total: 4, domains_done: 1, leads_new: 2 } }), domains: [{ domain: 'acme.ai', url: null, state: 'done', pages: 3, emails: 2, leads: 2, note: null }], log: [{ id: 1, message: 'Searched “q”: 10 results', created_at: 'x' }] } },
    })
    renderWithProviders(<DiscoverPage />)
    expect(await screen.findByTestId('run-progress')).toBeInTheDocument()
    expect(screen.getByLabelText('Who do you want to reach?')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Make a plan' })).toBeDisabled()
    expect(await screen.findByTestId('domain-acme.ai')).toHaveTextContent('Done')
    expect(screen.getByTestId('run-log')).toHaveTextContent('Searched “q”: 10 results')
    expect(screen.getByTestId('lead-counters')).toHaveTextContent('2')
    expect(screen.getByRole('button', { name: /Cancel/ })).toBeInTheDocument()
  })

  it('streams new log lines, domains and counters from live events without refetching', async () => {
    mockApi({
      'GET /discovery/runs': { json: [run({ state: 'running', stage: 'crawling' })] },
      'GET /discovery/runs/1': { json: { ...run({ state: 'running', stage: 'crawling', stats: { domains_total: 4 } }), domains: [], log: [] } },
    })
    renderWithProviders(<DiscoverPage />)
    await screen.findByTestId('run-progress')
    useLiveRuns.getState().applyLog({ run_id: 1, message: 'acme.ai/team: 2 email(s), 3 people' })
    useLiveRuns.getState().applyDomain({ run_id: 1, domain: 'acme.ai', state: 'crawling', pages: 1, emails: 0, leads: 0 })
    useLiveRuns.getState().applyRun({ run_id: 1, state: 'running', stage: 'crawling', stats: { domains_total: 4, leads_new: 7, by_confidence: { published_personal: 3, published_generic: 4, guessed: 0 } } })
    expect(await screen.findByText('acme.ai/team: 2 email(s), 3 people')).toBeInTheDocument()
    expect(screen.getByTestId('domain-acme.ai')).toHaveTextContent('Crawling')
    expect(screen.getByTestId('lead-counters')).toHaveTextContent('7')
    expect(screen.getByTestId('lead-counters')).toHaveTextContent('3 personal, 4 generic, 0 guessed')
  })

  it('cancels through the API', async () => {
    const { calls } = mockApi({
      'GET /discovery/runs': { json: [run({ state: 'running', stage: 'crawling' })] },
      'GET /discovery/runs/1': { json: { ...run({ state: 'running', stage: 'crawling' }), domains: [], log: [] } },
      'POST /discovery/runs/1/cancel': { json: run({ state: 'running' }) },
    })
    renderWithProviders(<DiscoverPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Cancel/ }))
    await waitFor(() => expect(calls.some((c) => c.path === '/discovery/runs/1/cancel')).toBe(true))
  })

  it('summarises a finished run and links to its leads', async () => {
    mockApi({
      'GET /discovery/runs': { json: [run({ stats: { leads_new: 32, domains_done: 40, leads_dup: 3, leads_suppressed: 1 }, leads_found: 32 })] },
      'GET /discovery/runs/1': { json: { ...run({ stats: { leads_new: 32, domains_done: 40, leads_dup: 3, leads_suppressed: 1 }, leads_found: 32 }), domains: [], log: [] } },
    })
    renderWithProviders(<DiscoverPage />)
    const past = await screen.findByRole('region', { name: 'Past searches' })
    await userEvent.click(within(past).getByRole('button', { name: /AI interns/ }))
    const sum = await screen.findByTestId('run-summary')
    expect(sum).toHaveTextContent('32 new leads from 40 sites, 3 already known, 1 suppressed')
    expect(within(sum).getByRole('link', { name: 'Review 32 new leads' })).toHaveAttribute('href', '/leads?run=1')
    expect(within(sum).getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
  })

  it('shows the reason when a search cannot start', async () => {
    mockApi({
      'GET /discovery/runs': { json: [] }, 'POST /discovery/plan': { json: PLAN },
      'POST /discovery/run': { status: 409, json: { detail: 'A search is already running. Wait for it to finish or cancel it.' } },
    })
    renderWithProviders(<DiscoverPage />)
    await userEvent.type(screen.getByLabelText('Who do you want to reach?'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Make a plan' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run search' }))
    // the plan stays on screen so nothing the user edited is lost
    await waitFor(() => expect(screen.getByTestId('plan-card')).toBeInTheDocument())
  })

  it('the example prompts fill the box', async () => {
    mockApi({ 'GET /discovery/runs': { json: [] } })
    renderWithProviders(<DiscoverPage />)
    await userEvent.click(screen.getByRole('button', { name: /Pre-seed investors who back AI developer tools/ }))
    expect(screen.getByLabelText('Who do you want to reach?')).toHaveValue('Pre-seed investors who back AI developer tools in India/SEA')
  })
})
