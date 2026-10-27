import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderWithProviders } from '@/test/utils'
import { CampaignsPage } from './campaigns-page'

const counts = { drafts_pending: 0, drafts_needs_review: 0, drafts_approved: 2, queued: 0, sending: 0, sent: 0, failed: 0, needs_confirmation: 0 }
const base = {
  template_id: null, mode: null, attachment_path: null, daily_cap: 25, window_start: '09:00', window_end: '18:00', delay_min_s: null, delay_max_s: null,
  followups_enabled: false, followup_days: [4, 9], followups_require_approval: false, pause_reason: null, paused_by: null, next_send_at: null,
  test_sent_at: null, dry_run_at: null, created_at: '2027-01-01 10:00:00', started_at: null, finished_at: null, warnings: [],
}

describe('campaigns page', () => {
  it('shows an empty state with a way to create a campaign', async () => {
    mockApi({ 'GET /campaigns': { json: [] } })
    renderWithProviders(<CampaignsPage />, '/campaigns')
    expect(await screen.findByText('No campaigns yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create your first campaign' })).toBeInTheDocument()
  })

  it('lists campaigns with state badges and opens the first one', async () => {
    mockApi({
      'GET /campaigns': { json: [{ id: 2, name: 'B', state: 'running', counts: { ...counts, sent: 1, queued: 3 }, ...base }, { id: 1, name: 'A', state: 'draft', counts, ...base }] },
      'GET /campaigns/2': { json: { id: 2, name: 'B', state: 'running', counts: { ...counts, sent: 1, queued: 3 }, ...base } },
      'GET /campaigns/2/readiness': { json: { ready: true, items: [] } },
      'GET /sending/status': { json: { paused_all: false, sent_today: 1, cap: 25, in_flight: 0, next_send_at: null, worker_alive: true, caffeinate_active: false } },
      'GET /campaigns/2/messages': { json: [] },
    })
    renderWithProviders(<CampaignsPage />, '/campaigns')
    expect((await screen.findAllByTestId('state-running')).length).toBeGreaterThan(0)
    expect(screen.getByTestId('state-draft')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'B', level: 2 })).toBeInTheDocument()
  })

  it('creates a campaign from the dialog and reports a server error inline', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /campaigns': { json: [] },
      'POST /campaigns': { status: 422, json: { detail: [{ loc: ['body', 'name'], msg: 'bad name' }] } },
    })
    renderWithProviders(<CampaignsPage />, '/campaigns')
    await user.click(await screen.findByRole('button', { name: 'Create your first campaign' }))
    await user.type(await screen.findByLabelText('Name'), 'X')
    await user.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByText(/bad name/)).toBeInTheDocument()
  })

  it('shows an inline error with Retry when the list fails', async () => {
    mockApi({ 'GET /campaigns': { status: 500, json: {} } })
    renderWithProviders(<CampaignsPage />, '/campaigns')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load campaigns')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
