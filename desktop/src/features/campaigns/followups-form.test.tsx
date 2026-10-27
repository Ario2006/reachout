import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Campaign, Template } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { validateFollowups } from './followups-fields'
import { missingFollowupBodies, orderTemplates } from './followups-template-picker'
import { SettingsForm } from './settings-form'

function campaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: 1, name: 'Spring outreach', template_id: null, mode: 'internship', attachment_path: null, daily_cap: 25,
    window_start: '09:00', window_end: '18:00', delay_min_s: null, delay_max_s: null, followups_enabled: false,
    followup_days: [4, 9], followups_require_approval: false, state: 'draft', pause_reason: null, paused_by: null,
    next_send_at: null, test_sent_at: null, dry_run_at: null, created_at: '2027-01-01 10:00:00', started_at: null, finished_at: null,
    counts: { drafts_pending: 0, drafts_needs_review: 0, drafts_approved: 0, queued: 0, sending: 0, sent: 0, failed: 0, needs_confirmation: 0 },
    warnings: [], ...over,
  }
}
const tpl = (id: number, name: string, mode: Template['mode'], f1: string | null, f2: string | null): Template => ({
  id, name, subject: 's', body: 'b', mode, followup_1_body: f1, followup_2_body: f2, is_starter: false,
} as Template)
const TEMPLATES = [
  tpl(1, 'Investor intro', 'investor', 'nudge', 'last nudge'),
  tpl(2, 'Internship ask', 'internship', 'nudge', null),
  tpl(3, 'Generic', null, null, null),
]

describe('follow-up validation', () => {
  const f = (day1: string, day2: string, enabled = true) => ({ enabled, day1, day2, requireApproval: false })
  it('accepts increasing days from 1 to 60', () => {
    expect(validateFollowups(f('1', '60'), [4, 9])).toEqual({ days: [1, 60] })
  })
  it.each([['0', '5'], ['4', '61'], ['4', '4'], ['9', '4'], ['x', '4'], ['', '9'], ['2.5', '9']])('rejects %s and %s', (a, b) => {
    expect(validateFollowups(f(a, b), [4, 9])).toHaveProperty('error')
  })
  it('does not validate when follow-ups are off and keeps the stored days', () => {
    expect(validateFollowups(f('0', '0', false), [4, 9])).toEqual({ days: [4, 9] })
  })
})

describe('template helpers', () => {
  it('puts mode-matched templates first and reports missing follow-up bodies', () => {
    const { matching, other } = orderTemplates(TEMPLATES, 'internship')
    expect(matching.map((t) => t.id)).toEqual([2])
    expect(other.map((t) => t.id)).toEqual([1, 3])
    expect(missingFollowupBodies(TEMPLATES[1], true)).toEqual(['follow-up 2'])
    expect(missingFollowupBodies(TEMPLATES[1], false)).toEqual([])
    expect(missingFollowupBodies(TEMPLATES[2], true)).toEqual(['follow-up 1', 'follow-up 2'])
  })
})

describe('campaign settings form: follow-ups and template', () => {
  it('saves the follow-up settings and template through PUT /campaigns/1', async () => {
    const { calls } = mockApi({ 'GET /templates': { json: TEMPLATES }, 'PUT /campaigns/1': { json: campaign() } })
    renderWithProviders(<SettingsForm campaign={campaign()} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('switch', { name: /send up to two follow-ups/i }))
    await user.clear(screen.getByLabelText(/follow-up 1, days/i)); await user.type(screen.getByLabelText(/follow-up 1, days/i), '3')
    await user.clear(screen.getByLabelText(/follow-up 2, days/i)); await user.type(screen.getByLabelText(/follow-up 2, days/i), '10')
    await user.click(screen.getByRole('switch', { name: /require my approval/i }))
    await user.selectOptions(await screen.findByLabelText('Template'), '2')
    await user.click(screen.getByRole('button', { name: /save settings/i }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({
      template_id: 2, followups_enabled: true, followup_days: [3, 10], followups_require_approval: true,
    })
  })

  it('shows an inline error and blocks saving when the days are not increasing', async () => {
    const { calls } = mockApi({ 'GET /templates': { json: TEMPLATES } })
    renderWithProviders(<SettingsForm campaign={campaign({ followups_enabled: true })} />)
    const user = userEvent.setup()
    await user.clear(screen.getByLabelText(/follow-up 2, days/i)); await user.type(screen.getByLabelText(/follow-up 2, days/i), '4')
    expect(await screen.findByTestId('followup-error')).toHaveTextContent(/later than follow-up 1/i)
    expect(screen.getByRole('button', { name: /save settings/i })).toBeDisabled()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('lists mode-matched templates first, links to Compose, and warns about a missing follow-up body', async () => {
    mockApi({ 'GET /templates': { json: TEMPLATES } })
    renderWithProviders(<SettingsForm campaign={campaign({ followups_enabled: true, template_id: 2 })} />)
    const select = await screen.findByLabelText('Template')
    const groups = Array.from(select.querySelectorAll('optgroup')).map((g) => g.label)
    expect(groups[0]).toMatch(/matches internship/i)
    expect(within(select.querySelector('optgroup') as HTMLElement).getByRole('option', { name: 'Internship ask' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /create or edit templates in compose/i })).toHaveAttribute('href', '/compose?tab=templates')
    expect(screen.getByTestId('template-followup-warning')).toHaveTextContent(/no body for follow-up 2/i)
  })

  it('does not warn when follow-ups are off', async () => {
    mockApi({ 'GET /templates': { json: TEMPLATES } })
    renderWithProviders(<SettingsForm campaign={campaign({ template_id: 3 })} />)
    await screen.findByLabelText('Template')
    expect(screen.queryByTestId('template-followup-warning')).toBeNull()
  })

  it('shows a Retry when templates fail to load', async () => {
    mockApi({ 'GET /templates': { status: 500, json: { detail: 'boom' } } })
    renderWithProviders(<SettingsForm campaign={campaign()} />)
    expect(await screen.findByText('boom')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
