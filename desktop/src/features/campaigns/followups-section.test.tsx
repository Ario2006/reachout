import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Campaign, FollowupPlan, FollowupRow } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { FollowupsSection } from './followups-section'
import { relativeDue } from './followups-table'

const CAMPAIGN = { id: 1, name: 'C', followups_enabled: true } as Campaign
const NOW = Date.now() / 1000
const row = (lead_id: number, over: Partial<FollowupRow> = {}): FollowupRow => ({
  lead_id, name: `Lead ${lead_id}`, email: `l${lead_id}@acme.ai`, kind: 'followup_1', due_at: NOW + 3 * 86400, state: 'waiting', reason: null, ...over,
})
const plan = (rows: FollowupRow[], counts: Record<string, number> = {}): FollowupPlan => ({ campaign_id: 1, rows, counts })

describe('relativeDue', () => {
  it('formats future and past times', () => {
    expect(relativeDue(1000 + 3 * 86400, 1000)).toBe('in 3 days')
    expect(relativeDue(1000, 1000 + 2 * 3600)).toBe('2 hours ago')
    expect(relativeDue(1000 + 30, 1000)).toBe('in under a minute')
  })
})

describe('follow-ups section', () => {
  it('shows counts, rows with state badges and the stop-on-reply explanation', async () => {
    mockApi({ 'GET /campaigns/1/followups': { json: plan([row(1), row(2, { state: 'skipped', reason: 'Replied', kind: 'followup_2' })], { waiting: 1, skipped: 1 }) } })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    expect(await screen.findByTestId('followup-row-1:followup_1')).toHaveTextContent(/in 3 days/)
    expect(screen.getByTestId('followup-count-waiting')).toHaveTextContent('1')
    expect(screen.getByTestId('followup-count-sent')).toHaveTextContent('0')
    const skipped = screen.getByTestId('followup-row-2:followup_2')
    expect(skipped).toHaveTextContent('Skipped')
    expect(skipped).toHaveTextContent('Replied')
    expect(within(skipped).queryByRole('button', { name: /skip/i })).toBeNull()
    expect(screen.getByText(/stop automatically when a lead replies, bounces or opts out/i)).toBeInTheDocument()
    expect(screen.queryByTestId('followups-review-link')).toBeNull()
  })

  it('skips only after confirmation, then reloads', async () => {
    let skipped = false
    const { calls } = mockApi({
      'GET /campaigns/1/followups': () => ({ json: plan(skipped ? [row(1, { state: 'skipped' })] : [row(1)], { waiting: skipped ? 0 : 1, skipped: skipped ? 1 : 0 }) }),
      'POST /campaigns/1/followups/skip': () => { skipped = true; return { status: 204 } },
    })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /skip follow-up 1 for lead 1/i }))
    expect(calls.some((c) => c.method === 'POST')).toBe(false) // nothing yet: the confirm dialog is open
    await user.click(await screen.findByRole('button', { name: 'Skip follow-up' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ lead_id: 1, kind: 'followup_1' }))
    await waitFor(() => expect(screen.getByTestId('followup-count-skipped')).toHaveTextContent('1'))
  })

  it('cancelling the confirm sends nothing', async () => {
    const { calls } = mockApi({ 'GET /campaigns/1/followups': { json: plan([row(1)], { waiting: 1 }) } })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /skip follow-up 1/i }))
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('links to the follow-up drafts in Compose when some await approval', async () => {
    mockApi({ 'GET /campaigns/1/followups': { json: plan([row(1, { state: 'awaiting_approval' })], { awaiting_approval: 2 }) } })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    const link = await screen.findByRole('link', { name: /review follow-up drafts/i })
    expect(link).toHaveAttribute('href', '/compose?campaign=1&kind=followups')
  })

  it('shows an empty state', async () => {
    mockApi({ 'GET /campaigns/1/followups': { json: plan([]) } })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    expect(await screen.findByTestId('followups-empty')).toHaveTextContent(/no follow-ups are scheduled yet/i)
  })

  it('handles 501 as "not available yet" and Retry recovers', async () => {
    let ready = false
    mockApi({ 'GET /campaigns/1/followups': () => (ready ? { json: plan([row(1)], { waiting: 1 }) } : { status: 501, json: { detail: 'milestone 6: follow-ups' } }) })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    expect(await screen.findByText(/not available yet/i)).toBeInTheDocument()
    ready = true
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('followup-row-1:followup_1')).toBeInTheDocument()
  })

  it('shows other errors inline with Retry', async () => {
    mockApi({ 'GET /campaigns/1/followups': { status: 500, json: { detail: 'database is locked' } } })
    renderWithProviders(<FollowupsSection campaign={CAMPAIGN} />)
    expect(await screen.findByText(/database is locked/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
