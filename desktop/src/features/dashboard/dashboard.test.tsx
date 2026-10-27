import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { ActivityItem, Metrics } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { DashboardPage } from './dashboard-page'

const metrics = (over: Partial<Metrics['kpis']> = {}, attention: Metrics['attention'] = []): Metrics => ({
  kpis: { leads: 120, drafts_awaiting_review: 4, sent_today: 12, daily_cap: 25, sent_total: 60, replies: 9, reply_rate: 15.8, bounce_rate: 3.3, bounced: 2, opted_out: 1, followups_sent: 5, awaiting_followup: 7, delivered: 58, ...over },
  funnel: [{ stage: 'leads', count: 120 }, { stage: 'drafted', count: 80 }, { stage: 'approved', count: 70 }, { stage: 'sent', count: 60 }, { stage: 'delivered', count: 58 }, { stage: 'replied', count: 9 }],
  per_day: [{ day: '2026-10-26', sent: 10, replies: 1, bounces: 0 }, { day: '2026-10-27', sent: 20, replies: 3, bounces: 1 }, { day: '2026-10-28', sent: 0, replies: 0, bounces: 0 }],
  attention, opens_note: 'Opens are not tracked: it is unreliable and would need a public server.',
})
const feed: ActivityItem[] = [
  { at: '2026-10-27T05:00:00Z', kind: 'reply', text: 'Priya Sharma replied', lead_id: 3 },
  { at: '2026-10-27T04:00:00Z', kind: 'sent', text: 'Sent an email to Ravi', lead_id: 4 },
]

describe('DashboardPage', () => {
  it('renders KPI cards, funnel, chart, attention list, feed and the opens note', async () => {
    mockApi({
      'GET /metrics': { json: metrics({}, [
        { kind: 'bounce_rate', severity: 'critical', message: 'Bounce rate is 8.0%, above the 5% limit.', count: 2, link: '/campaigns' },
        { kind: 'unread_replies', severity: 'info', message: '3 unread reply(ies).', count: 3, link: '/inbox?filter=unread' },
      ]) },
      'GET /activity': { json: feed },
    })
    renderWithProviders(<DashboardPage />, '/')
    expect(await screen.findByTestId('kpi-leads')).toHaveTextContent('120')
    expect(screen.getByTestId('kpi-today')).toHaveTextContent('12 / 25')
    expect(screen.getByTestId('kpi-reply-rate')).toHaveTextContent('15.8%')
    expect(screen.getByTestId('kpi-reply-rate')).toHaveTextContent('of 58 delivered')
    expect(screen.getByTestId('kpi-bounce-rate')).toHaveTextContent('3.3%')
    const funnel = within(screen.getByTestId('funnel'))
    expect(funnel.getAllByRole('listitem')).toHaveLength(6)
    expect(funnel.getByText(/Replied/)).toHaveTextContent('9')
    expect(screen.getByRole('figure', { name: 'Emails sent per day' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Bounce rate is 8.0%/ })).toHaveAttribute('href', '/campaigns')
    expect(screen.getByRole('link', { name: /3 unread/ })).toHaveAttribute('href', '/inbox?filter=unread')
    expect(await screen.findByText('Priya Sharma replied')).toBeInTheDocument()
    expect(screen.getByTestId('opens-note')).toHaveTextContent('Opens are not tracked')
  })

  it('shows dashes instead of fake rates before there is data, and empty states', async () => {
    mockApi({
      'GET /metrics': { json: { ...metrics({ reply_rate: null, bounce_rate: null, sent_total: 0, delivered: 0, replies: 0, sent_today: 0 }), per_day: [] } },
      'GET /activity': { json: [] },
    })
    renderWithProviders(<DashboardPage />, '/')
    expect(await screen.findByTestId('kpi-reply-rate')).toHaveTextContent('–')
    expect(screen.getByTestId('kpi-bounce-rate')).toHaveTextContent('Not enough sends yet')
    expect(screen.getByTestId('attention-empty')).toBeInTheDocument()
    expect(await screen.findByTestId('feed-empty')).toBeInTheDocument()
  })

  it('shows skeletons first, then an inline error with Retry', async () => {
    let fail = true
    mockApi({
      'GET /metrics': () => (fail ? { status: 500, json: { detail: 'db is busy' } } : { json: metrics() }),
      'GET /activity': { json: feed },
    })
    renderWithProviders(<DashboardPage />, '/')
    expect(screen.getByLabelText('Loading metrics')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('db is busy')
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('kpi-leads')).toBeInTheDocument()
  })

  it('asks the API for the chosen range', async () => {
    const { calls } = mockApi({ 'GET /metrics': { json: metrics() }, 'GET /activity': { json: feed } })
    renderWithProviders(<DashboardPage />, '/')
    await screen.findByTestId('kpi-leads')
    await userEvent.click(screen.getByRole('button', { name: '7 days' }))
    await screen.findByRole('button', { name: '7 days', pressed: true })
    expect(calls.some((c) => c.path.startsWith('/metrics') && c.path.includes('range=7d'))).toBe(true)
  })

  it('gives the sends chart a total and a per-day table for assistive tech', async () => {
    mockApi({ 'GET /metrics': { json: metrics() }, 'GET /activity': { json: feed } })
    renderWithProviders(<DashboardPage />, '/')
    await screen.findByTestId('kpi-leads')
    expect(screen.getByTestId('sends-readout')).toHaveTextContent('30 emails in this period')
    const rows = within(screen.getByRole('table', { name: 'Emails sent per day' })).getAllByRole('row')
    expect(rows[2]).toHaveTextContent(/20\s*3\s*1/)
  })

})
