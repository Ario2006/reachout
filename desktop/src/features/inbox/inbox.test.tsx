import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { InboxStatus, ThreadDetail, ThreadsPage } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { InboxPage } from './inbox-page'

const lead = (id: number, over: Record<string, unknown> = {}) => ({
  id, name: `Person ${id}`, email: `p${id}@acme.test`, company: `Co ${id}`, role: 'CTO', email_confidence: 'published_personal' as const,
  status: 'replied' as const, outcome: null, ...over,
})

const page = (items: ThreadsPage['items'], unread = 0): ThreadsPage => ({ items, total: items.length, unread_total: unread })
const summary = (id: number, over: Partial<ThreadsPage['items'][number]> = {}): ThreadsPage['items'][number] => ({
  lead: lead(id), last_activity_at: '2026-10-27T05:00:00Z', last_kind: 'reply', snippet: 'Please send your resume', unread: 0, opt_out_to_confirm: false, ...over,
})
const idle: InboxStatus = { last_checked_at: Math.floor(Date.now() / 1000) - 180, next_check_at: null, last_error: null, checking: false }

const detail = (over: Partial<ThreadDetail> = {}): ThreadDetail => ({
  lead: lead(1),
  entries: [
    { kind: 'sent', at: '2026-10-27T04:00:00Z', subject: 'Internship at Acme', text: 'Hi, I would like to help.', message_id: 1, message_state: 'sent', send_kind: 'initial', unread: false, needs_confirmation: false },
    { kind: 'reply', at: '2026-10-27T05:00:00Z', subject: 'Re: Internship at Acme', text: 'Please send your resume', event_id: 7, unread: true, needs_confirmation: false },
    { kind: 'auto_reply', at: '2026-10-27T05:05:00Z', subject: 'Out of office', text: 'Back on Monday', event_id: 8, unread: false, needs_confirmation: false },
    { kind: 'bounce', at: '2026-10-27T05:06:00Z', subject: 'Delivery Status Notification', text: 'Delivery failed (5.1.1)', event_id: 9, status_code: '5.1.1', unread: false, needs_confirmation: false },
  ],
  gmail_url: 'https://mail.google.com/mail/#search/rfc822msgid%3Aabc',
  ...over,
})

describe('InboxPage', () => {
  it('lists conversations with unread dots and the opt-out flag', async () => {
    mockApi({
      'GET /threads': { json: page([summary(1, { unread: 2 }), summary(2, { opt_out_to_confirm: true, last_kind: 'opt_out', snippet: 'STOP' })], 2) },
      'GET /inbox/status': { json: idle },
    })
    renderWithProviders(<InboxPage />, '/inbox')
    expect(await screen.findByText('Person 1')).toBeInTheDocument()
    expect(screen.getAllByTestId('thread-row')).toHaveLength(2)
    expect(screen.getByLabelText('2 unread')).toBeInTheDocument()
    expect(screen.getByText('Check opt-out')).toBeInTheDocument()
    expect(screen.getByText(/2 conversations, 2 unread/)).toBeInTheDocument()
    expect(await screen.findByText(/Last checked 3m ago/)).toBeInTheDocument()
  })

  it('shows an empty state, and a retry when loading fails', async () => {
    let fail = true
    mockApi({
      'GET /threads': () => (fail ? { status: 500, json: { detail: 'boom' } } : { json: page([]) }),
      'GET /inbox/status': { json: { ...idle, last_checked_at: null } },
    })
    renderWithProviders(<InboxPage />, '/inbox')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load conversations')
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('inbox-empty')).toHaveTextContent('No conversations yet')
    expect(screen.getByText('Not checked yet')).toBeInTheDocument()
  })

  it('sends the chosen filter to the API', async () => {
    const { calls } = mockApi({ 'GET /threads': { json: page([]) }, 'GET /inbox/status': { json: idle } })
    renderWithProviders(<InboxPage />, '/inbox?filter=unread')
    await screen.findByTestId('inbox-empty')
    expect(calls.some((c) => c.path.startsWith('/threads?') && c.path.includes('filter=unread'))).toBe(true)
    expect(screen.getByRole('button', { name: 'Unread' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Nothing matches this filter.')).toBeInTheDocument()
  })

  it('shows the conversation with auto-replies and bounces told apart, and a Gmail link', async () => {
    mockApi({
      'GET /threads': { json: page([summary(1)]) }, 'GET /inbox/status': { json: idle }, 'GET /threads/1': { json: detail() },
    })
    renderWithProviders(<InboxPage />, '/inbox?lead=1')
    expect(await screen.findByTestId('entry-reply')).toHaveTextContent('Please send your resume')
    expect(screen.getByTestId('entry-sent')).toHaveTextContent('You wrote')
    const auto = screen.getByTestId('entry-auto_reply')
    expect(auto).toHaveTextContent('Automatic reply (not a real answer)')
    expect(auto.className).toContain('border-dashed')
    expect(screen.getByTestId('entry-bounce')).toHaveTextContent('Delivery failed (5.1.1)')
    expect(screen.getByTestId('entry-reply')).toHaveTextContent('New')
    const link = screen.getByRole('link', { name: /Open in Gmail/ })
    expect(link).toHaveAttribute('href', 'https://mail.google.com/mail/#search/rfc822msgid%3Aabc')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('never renders a non-http Gmail link', async () => {
    mockApi({ 'GET /threads': { json: page([summary(1)]) }, 'GET /inbox/status': { json: idle }, 'GET /threads/1': { json: detail({ gmail_url: 'javascript:alert(1)' }) } })
    renderWithProviders(<InboxPage />, '/inbox?lead=1')
    await screen.findByTestId('thread-view')
    expect(screen.queryByRole('link', { name: /Open in Gmail/ })).not.toBeInTheDocument()
  })

  it('quick status buttons post the right change', async () => {
    const { calls } = mockApi({
      'GET /threads': { json: page([summary(1)]) }, 'GET /inbox/status': { json: idle }, 'GET /threads/1': { json: detail() },
      'POST /leads/1/status': { json: lead(1) },
    })
    renderWithProviders(<InboxPage />, '/inbox?lead=1')
    await screen.findByTestId('thread-view')
    const bodies = () => calls.filter((c) => c.method === 'POST').map((c) => c.body)
    await userEvent.click(screen.getByRole('button', { name: 'Interested' }))
    await waitFor(() => expect(bodies()).toContainEqual({ outcome: 'interested' }))
    await userEvent.click(screen.getByRole('button', { name: 'Not interested' }))
    await waitFor(() => expect(bodies()).toContainEqual({ outcome: 'not_interested', status: 'closed' }))
    await userEvent.click(screen.getByRole('button', { name: 'Meeting booked' }))
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(bodies()).toEqual([{ outcome: 'interested' }, { outcome: 'not_interested', status: 'closed' }, { outcome: 'meeting_booked' }, { status: 'closed' }]))
  })

  it('shows the reason when the server refuses a status', async () => {
    mockApi({
      'GET /threads': { json: page([summary(1)]) }, 'GET /inbox/status': { json: idle }, 'GET /threads/1': { json: detail() },
      'POST /leads/1/status': { status: 409, json: { detail: 'This lead is opted out; it can not be reopened.' } },
    })
    renderWithProviders(<InboxPage />, '/inbox?lead=1')
    await screen.findByTestId('thread-view')
    await userEvent.click(screen.getByRole('button', { name: 'Interested' }))
    expect(await screen.findByText('This lead is opted out; it can not be reopened.')).toBeInTheDocument()
  })

  it('highlights an opt-out reply and confirms it in one click', async () => {
    const optOut = detail({
      lead: lead(2, { status: 'opted_out' }),
      entries: [{ kind: 'opt_out', at: '2026-10-27T05:00:00Z', subject: 'Re: x', text: 'STOP', event_id: 5, unread: true, needs_confirmation: true }],
    })
    const { calls } = mockApi({
      'GET /threads': { json: page([summary(2, { opt_out_to_confirm: true })]) }, 'GET /inbox/status': { json: idle },
      'GET /threads/2': { json: optOut }, 'POST /events/5/confirm-opt-out': { status: 204 },
    })
    renderWithProviders(<InboxPage />, '/inbox?lead=2')
    const entry = await screen.findByTestId('entry-opt_out')
    expect(entry).toHaveTextContent('already suppressed')
    expect(entry.className).toContain('amber')
    await userEvent.click(within(entry).getByRole('button', { name: 'Confirm opt-out' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/events/5/confirm-opt-out')).toBe(true))
  })

  it('Check now starts a check and reports a login problem', async () => {
    let status: InboxStatus = { ...idle, last_error: 'Gmail login failed: Gmail rejected the login.' }
    const { calls } = mockApi({
      'GET /threads': { json: page([]) }, 'GET /inbox/status': () => ({ json: status }),
      'POST /inbox/check-now': () => { status = { ...idle, checking: true }; return { status: 202, json: { id: 1 } } },
    })
    renderWithProviders(<InboxPage />, '/inbox')
    expect(await screen.findByTestId('inbox-error')).toHaveTextContent('Gmail login failed')
    await userEvent.click(screen.getByRole('button', { name: /Check now/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/inbox/check-now')).toBe(true))
    expect(await screen.findByText('Checking Gmail...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Check now/ })).toBeDisabled()
  })

  it('shows a retry when a conversation fails to load', async () => {
    mockApi({ 'GET /threads': { json: page([summary(1)]) }, 'GET /inbox/status': { json: idle }, 'GET /threads/1': { status: 500, json: { detail: 'nope' } } })
    renderWithProviders(<InboxPage />, '/inbox?lead=1')
    expect(await screen.findByText(/Could not load this conversation/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
