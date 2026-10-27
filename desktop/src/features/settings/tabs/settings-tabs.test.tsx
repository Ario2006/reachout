import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderWithProviders, settingsFixture } from '@/test/utils'
import { CrawlingTab } from './crawling-tab'
import { SendingTab } from './sending-tab'
import { SuppressionTab } from './suppression-tab'

describe('SendingTab', () => {
  it('limits emails per company: shows the safe defaults, rejects out-of-range values, and saves the change', async () => {
    const { calls } = mockApi({ 'PUT /settings': { json: settingsFixture({ max_per_domain_per_day: 3 }) }, 'GET /setup/status': { json: {} } })
    renderWithProviders(<SendingTab data={settingsFixture()} />)
    const per = screen.getByLabelText('Emails per company per day')
    expect(per).toHaveValue(2)
    expect(screen.getByLabelText('Minutes between people at one company')).toHaveValue(60)
    expect(screen.getByRole('switch', { name: /recipient's time zone/ })).toBeChecked()
    await userEvent.clear(per)
    await userEvent.type(per, '50')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('At most 10')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
    await userEvent.clear(per)
    await userEvent.type(per, '3')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ max_per_domain_per_day: 3, min_minutes_between_same_domain: 60, use_recipient_timezone: true }))
  })

  it('blocks values above the hard ceiling with an inline error and no request', async () => {
    const { calls } = mockApi({})
    renderWithProviders(<SendingTab data={settingsFixture()} />)
    const cap = screen.getByLabelText('Daily cap')
    await userEvent.clear(cap)
    await userEvent.type(cap, '500')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('At most 100')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('warns inline above 25 and saves the new values, showing server warnings', async () => {
    const { calls } = mockApi({
      'PUT /settings': { json: settingsFixture({ daily_cap: 30 }, { warnings: ['Daily cap 30 is above the safe default of 25.'] }) },
      'GET /setup/status': { json: {} },
    })
    renderWithProviders(<SendingTab data={settingsFixture()} />)
    const cap = screen.getByLabelText('Daily cap')
    await userEvent.clear(cap)
    await userEvent.type(cap, '30')
    expect(screen.getByText(/Above the safe default of 25/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Daily cap 30 is above the safe default of 25.')).toBeInTheDocument()
    const put = calls.find((c) => c.method === 'PUT')
    expect(put?.body).toMatchObject({ daily_cap: 30, delay_min_s: 60, window_start: '09:00' })
    expect(put?.body).not.toHaveProperty('gmail_address')
  })

  it('validates delay and window ordering', async () => {
    mockApi({})
    renderWithProviders(<SendingTab data={settingsFixture()} />)
    const min = screen.getByLabelText(/Minimum delay/)
    await userEvent.clear(min)
    await userEvent.type(min, '500')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Must be at least the minimum delay')).toBeInTheDocument()
  })

  it('shows server-side field errors from a 422', async () => {
    mockApi({
      'PUT /settings': { status: 422, json: { detail: [{ loc: ['body', 'window_end'], msg: 'window_end must be after window_start' }] } },
    })
    renderWithProviders(<SendingTab data={settingsFixture()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Mon' })) // makes the form dirty
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('window_end must be after window_start')).toBeInTheDocument()
  })

  it('requires at least one send day', async () => {
    mockApi({})
    renderWithProviders(<SendingTab data={settingsFixture({ send_days: [0] })} />)
    await userEvent.click(screen.getByRole('button', { name: 'Mon' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Pick at least one day')).toBeInTheDocument()
  })
})

const row = (email: string, reason: string, removable: boolean) => ({ email, reason, added_at: '2026-10-25 10:00:00', removable })

describe('SuppressionTab', () => {
  it('lists entries; only manual ones have a remove button, automatic ones are protected', async () => {
    mockApi({ 'GET /suppression': { json: [row('manual@x.com', 'manual', true), row('bounced@x.com', 'bounce', false)] } })
    renderWithProviders(<SuppressionTab />)
    expect(await screen.findByText('manual@x.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove manual@x.com' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove bounced@x.com' })).toBeNull()
    expect(screen.getByLabelText('Protected: added automatically')).toBeInTheDocument()
  })

  it('needs the exact address typed before Remove is enabled, then confirms to the API', async () => {
    const { calls } = mockApi({
      'GET /suppression': { json: [row('manual@x.com', 'manual', true)] },
      'DELETE /suppression': { status: 204 },
    })
    renderWithProviders(<SuppressionTab />)
    await userEvent.click(await screen.findByRole('button', { name: 'Remove manual@x.com' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: 'Remove' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Type the email to confirm'), 'manual@x.co')
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Type the email to confirm'), 'm')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(calls.find((c) => c.method === 'DELETE')?.path).toBe('/suppression?email=manual%40x.com&confirm=manual%40x.com')
  })

  it('adds an address and reports server errors', async () => {
    const { calls } = mockApi({
      'GET /suppression': { json: [] },
      'POST /suppression': { json: row('new@x.com', 'manual', true), status: 201 },
    })
    renderWithProviders(<SuppressionTab />)
    expect(await screen.findByText(/Nobody is suppressed yet/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Email to suppress'), 'new@x.com')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ email: 'new@x.com' }))
  })
})


describe('CrawlingTab', () => {
  it('saves the blocklist as a clean list of domains plus the crawl limits', async () => {
    const { calls } = mockApi({ 'PUT /settings': { json: settingsFixture() }, 'GET /setup/status': { json: {} } })
    renderWithProviders(<CrawlingTab data={settingsFixture({ domain_blocklist_extra: ['old.com'] })} />)
    const box = screen.getByLabelText('Sites to never crawl')
    expect(box).toHaveValue('old.com')
    await userEvent.clear(box)
    await userEvent.type(box, 'spam.io, junk.net{Enter}third.org')
    const cap = screen.getByLabelText('Sites per search')
    await userEvent.clear(cap)
    await userEvent.type(cap, '90')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const body = calls.find((c) => c.method === 'PUT')?.body as Record<string, unknown>
    expect(body['domain_blocklist_extra']).toEqual(['spam.io', 'junk.net', 'third.org'])
    expect(body['max_domains_per_run']).toBe(90)
    expect(body).not.toHaveProperty('blocklist_text')
  })

  it('rejects out-of-range limits inline without calling the API', async () => {
    const { calls } = mockApi({})
    renderWithProviders(<CrawlingTab data={settingsFixture()} />)
    const c = screen.getByLabelText('Sites crawled at once')
    await userEvent.clear(c)
    await userEvent.type(c, '99')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('At most 64')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })
})
