import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Campaign, Readiness } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { CampaignDetail } from './campaign-detail'
import { capWarning, formatCountdown, progressOf } from './helpers'

function campaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: 1, name: 'Spring outreach', template_id: null, mode: 'internship', attachment_path: null, daily_cap: 25,
    window_start: '09:00', window_end: '18:00', delay_min_s: null, delay_max_s: null, followups_enabled: false,
    followup_days: [4, 9], followups_require_approval: false, state: 'draft', pause_reason: null, paused_by: null,
    next_send_at: null, test_sent_at: null, dry_run_at: null, created_at: '2027-01-01 10:00:00', started_at: null, finished_at: null,
    counts: { drafts_pending: 0, drafts_needs_review: 0, drafts_approved: 3, queued: 0, sending: 0, sent: 0, failed: 0, needs_confirmation: 0 },
    warnings: [], ...over,
  }
}

const READY: Readiness = {
  ready: true,
  items: [
    { id: 'gmail', label: 'Gmail is connected', ok: true, detail: 'Sending as me@gmail.com', blocking: true },
    { id: 'approved_drafts', label: 'Drafts are approved', ok: true, detail: '3 approved draft(s).', blocking: true },
    { id: 'attachment', label: 'Attachment is fine', ok: true, detail: 'No attachment.', blocking: false },
    { id: 'test_sent', label: 'A test email was sent to you', ok: false, detail: 'Send a test to yourself first (recommended).', blocking: false },
  ],
}
const NOT_READY: Readiness = {
  ready: false,
  items: [
    { ...READY.items[0]!, ok: false, detail: 'No App Password is saved for this address. Add it in Settings.' },
    { ...READY.items[1]!, ok: false, detail: 'Approve at least one draft in Compose.' },
  ],
}
const SENDING = { paused_all: false, sent_today: 2, cap: 25, in_flight: 0, next_send_at: null, worker_alive: true, caffeinate_active: false }

function routes(c: Campaign, readiness: Readiness, extra: Record<string, unknown> = {}) {
  return {
    'GET /campaigns/1': { json: c },
    'GET /campaigns/1/readiness': { json: readiness },
    'GET /sending/status': { json: SENDING },
    'GET /campaigns/1/messages': { json: [] },
    ...extra,
  }
}

describe('campaign detail', () => {
  it('shows the readiness checklist and enables Start when ready', async () => {
    mockApi(routes(campaign(), READY))
    renderWithProviders(<CampaignDetail id={1} />)
    expect(await screen.findByText('Spring outreach')).toBeInTheDocument()
    expect(await screen.findByTestId('ready-gmail')).toHaveAttribute('data-ok', 'true')
    expect(screen.getByTestId('ready-test_sent')).toHaveAttribute('data-ok', 'false')
    expect(screen.getByRole('button', { name: /start/i })).toBeEnabled()
    expect(screen.queryByTestId('start-reason')).toBeNull()
  })

  it('disables Start and says why when the campaign is not ready', async () => {
    mockApi(routes(campaign({ counts: { ...campaign().counts, drafts_approved: 0 } }), NOT_READY))
    renderWithProviders(<CampaignDetail id={1} />)
    const reason = await screen.findByTestId('start-reason')
    expect(reason).toHaveTextContent('No App Password is saved')
    expect(screen.getByRole('button', { name: /start/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /send test to myself/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /dry run/i })).toBeDisabled()
  })

  it('starts the campaign and shows a server refusal inline', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi(routes(campaign(), READY, { 'POST /campaigns/1/start': { status: 409, json: { detail: 'Sending is paused for all campaigns. Turn it back on first.' } } }))
    renderWithProviders(<CampaignDetail id={1} />)
    await user.click(await screen.findByRole('button', { name: /start/i }))
    expect(await screen.findByTestId('start-summary')).toHaveTextContent('3 approved emails will be queued: at most 25 a day, between 09:00 and 18:00')
    await user.click(screen.getByRole('button', { name: 'Start sending' }))
    expect(await screen.findByTestId('action-error')).toHaveTextContent('paused for all campaigns')
    expect(calls.some((c) => c.method === 'POST' && c.path === '/campaigns/1/start')).toBe(true)
  })

  it('sends a test to myself', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi(routes(campaign(), READY, { 'POST /campaigns/1/test-send': { json: { to: 'me@gmail.com', message_id: '<x@y>' } } }))
    renderWithProviders(<CampaignDetail id={1} />)
    await user.click(await screen.findByRole('button', { name: /send test to myself/i }))
    await waitFor(() => expect(calls.some((c) => c.path === '/campaigns/1/test-send')).toBe(true))
  })

  it('runs a dry run and lists each decision', async () => {
    const user = userEvent.setup()
    mockApi(routes(campaign(), READY, {
      'POST /campaigns/1/dry-run': { status: 202, json: { id: 9, kind: 'campaign_dry_run', state: 'queued', progress: 0, total: 0, payload: {}, cancel_requested: false, created_at: 'x' } },
      'GET /jobs/9': {
        json: {
          id: 9, kind: 'campaign_dry_run', state: 'succeeded', progress: 2, total: 2, payload: {}, cancel_requested: false, created_at: 'x',
          result: { would_send: 1, blocked: 1, lines: [
            { lead_id: 1, email: 'ok@good.test', subject: 'Hello', decision: 'would_send', reason: null },
            { lead_id: 2, email: 'no@bad.test', subject: 'Hi', decision: 'blocked', reason: 'This address is on the suppression list.' },
          ] },
        },
      },
    }))
    renderWithProviders(<CampaignDetail id={1} />)
    await user.click(await screen.findByRole('button', { name: /dry run/i }))
    expect(await screen.findByTestId('dry-summary')).toHaveTextContent('1 would be sent, 1 blocked')
    expect(screen.getByText('This address is on the suppression list.')).toBeInTheDocument()
  })

  it('shows progress, the countdown and the awake note while running, with Pause', async () => {
    const soon = Date.now() / 1000 + 95
    mockApi({
      ...routes(campaign({ state: 'running', next_send_at: soon, counts: { ...campaign().counts, sent: 2, queued: 6 } }), READY),
      'GET /sending/status': { json: { ...SENDING, caffeinate_active: true } },
    })
    renderWithProviders(<CampaignDetail id={1} />)
    expect(await screen.findByText('2 of 8 emails sent')).toBeInTheDocument()
    expect(screen.getByTestId('next-send')).toHaveTextContent(/Next email in 1m/)
    expect(await screen.findByTestId('caffeinate')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^start/i })).toBeNull()
    expect(screen.getByLabelText('Daily cap')).toBeDisabled() // locked while running
  })

  it('shows the auto-pause banner with the reason and offers Resume', async () => {
    mockApi(routes(campaign({
      state: 'paused', paused_by: 'bounce_rate',
      pause_reason: 'Paused automatically: 2 of 20 emails bounced (10%), above the 5% limit. Check the addresses in this campaign before resuming.',
    }), READY))
    renderWithProviders(<CampaignDetail id={1} />)
    const banner = await screen.findByTestId('pause-banner')
    expect(banner).toHaveTextContent('too many bounces')
    expect(banner).toHaveTextContent('2 of 20 emails bounced')
    expect(screen.getByRole('button', { name: /resume/i })).toBeEnabled()
  })

  it('lists emails that need confirmation with Confirm sent / Not sent, and failures with Retry', async () => {
    const user = userEvent.setup()
    const unsure = { id: 5, campaign_id: 1, lead_id: 1, lead_name: 'Ada', recipient_email: 'ada@x.test', kind: 'initial', state: 'needs_confirmation', subject: 'Hi', sent_at: null, error: 'stopped' }
    const failed = { ...unsure, id: 6, lead_name: 'Bob', recipient_email: 'bob@x.test', state: 'failed', error: '550 no such user' }
    const { calls } = mockApi({
      ...routes(campaign({ state: 'paused', paused_by: 'user', pause_reason: 'You paused this campaign.' }), READY),
      'GET /campaigns/1/messages': (call) => ({ json: call.path.includes('needs_confirmation') ? [unsure] : call.path.includes('failed') ? [failed] : [] }),
      'POST /messages/5/confirm': { json: { ...unsure, state: 'sent' } },
      'POST /messages/6/retry': { json: { ...failed, state: 'queued' } },
    })
    renderWithProviders(<CampaignDetail id={1} />)
    const row = await screen.findByTestId('msg-5')
    expect(row).toHaveTextContent('never resent on its own')
    await user.click(screen.getByRole('button', { name: 'Confirm sent' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/messages/5/confirm')?.body).toEqual({ was_sent: true }))
    // "Not sent" re-queues the email, so it asks first and explains that it may be sent again
    await user.click(screen.getByRole('button', { name: 'Not sent' }))
    expect(await screen.findByText(/receives it twice/)).toBeInTheDocument()
    expect(calls.filter((c) => c.path === '/messages/5/confirm')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Yes, it was not sent' }))
    await waitFor(() => expect(calls.filter((c) => c.path === '/messages/5/confirm')[1]?.body).toEqual({ was_sent: false }))
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/messages/6/retry')).toBe(true))
  })

  it('warns above the recommended daily cap and validates before saving', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi(routes(campaign(), READY))
    renderWithProviders(<CampaignDetail id={1} />)
    const cap = await screen.findByLabelText('Daily cap')
    await user.clear(cap)
    await user.type(cap, '60')
    expect(screen.getByText(/Above the recommended 25 a day/)).toBeInTheDocument()
    await user.clear(cap)
    await user.type(cap, '500')
    await user.click(screen.getByRole('button', { name: /save settings/i }))
    expect(await screen.findByText(/whole number from 1 to 100/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('shows an inline error with Retry when the campaign cannot be loaded', async () => {
    const user = userEvent.setup()
    let n = 0
    mockApi({ ...routes(campaign(), READY), 'GET /campaigns/1': () => (n++ === 0 ? { status: 500, json: {} } : { json: campaign() }) })
    renderWithProviders(<CampaignDetail id={1} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this campaign')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Spring outreach')).toBeInTheDocument()
  })

  it('leaves a marked slot for the follow-up schedule', async () => {
    mockApi(routes(campaign(), READY))
    renderWithProviders(<CampaignDetail id={1} />)
    expect(await screen.findByLabelText('Follow-ups')).toHaveAttribute('data-slot', 'followups')
  })
})

describe('helpers', () => {
  it('formats countdowns and progress', () => {
    expect(formatCountdown(5)).toBe('5s')
    expect(formatCountdown(95)).toBe('1m 35s')
    expect(formatCountdown(3700)).toBe('1h 1m')
    expect(progressOf({ ...campaign().counts, sent: 1, queued: 3 })).toEqual({ sent: 1, total: 4, percent: 25 })
    expect(progressOf(campaign().counts).percent).toBe(0)
    expect(capWarning(25)).toBeNull()
    expect(capWarning(26)).toMatch(/recommended/)
  })
})
