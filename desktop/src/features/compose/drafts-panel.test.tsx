import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Campaign, DraftOut } from '@/lib/api/types'
import { mockApi, renderWithProviders, type Call } from '@/test/utils'
import { DraftsPanel } from './drafts-panel'

const CAMPAIGN = { id: 1, name: 'Interns', template_id: 3, state: 'draft' } as Campaign
const OPENER = 'I liked that Acme builds developer tools.'

const draft = (id: number, over: Partial<DraftOut> = {}): DraftOut => ({
  id, campaign_id: 1, kind: 'initial', subject: `Hello ${id}`, body: `Hi P${id},\n\n${OPENER}\n\nThanks`, opener: OPENER, opener_source: 'llm',
  opener_span: { start: `Hi P${id},\n\n`.length, end: `Hi P${id},\n\n`.length + OPENER.length }, flags: [], status: 'pending', updated_at: '2026-10-26 10:00:00',
  lead: { id, name: `Person ${id}`, email: `p${id}@acme.ai`, role: 'CTO', company: 'Acme', email_confidence: 'published_personal', context_snippet: 'Acme builds developer tools.', source_url: 'https://acme.ai/team' },
  ...over,
})

/** A tiny stateful backend for /drafts so refetches after a PATCH see the new status. */
function backend(initial: DraftOut[]) {
  const rows = new Map(initial.map((d) => [d.id, d]))
  const counts = () => {
    const c = { pending: 0, needs_review: 0, approved: 0, rejected: 0 }
    for (const d of rows.values()) c[d.status] += 1
    return c
  }
  const route = (key: string, call: Call) => {
    const url = new URL('http://x' + call.path)
    const one = /^(GET|PATCH) \/drafts\/(\d+)$/.exec(key)
    if (one) {
      const d = rows.get(Number(one[2]))
      if (!d) return { status: 404, json: { detail: 'Draft not found' } }
      if (one[1] === 'PATCH') {
        const body = call.body as { status?: DraftOut['status'] }
        if (body.status) rows.set(d.id, { ...d, status: body.status })
      }
      return { json: { ...rows.get(d.id), final_text: rows.get(d.id)?.body + '\n\n-- \nSam\nIf you would rather not hear from me, reply stop.\n' } }
    }
    if (key === 'GET /drafts') {
      const want = url.searchParams.getAll('status')
      const items = [...rows.values()].filter((d) => want.length === 0 || want.includes(d.status))
      return { json: { items, total: items.length, counts: counts() } }
    }
    if (key === 'POST /drafts/bulk-approve') return { json: { approved: 2, skipped_flagged: 1, skipped_blocked: 0 } }
    throw new Error(`Unmocked request: ${key}`)
  }
  const routes = new Proxy({}, { get: (_t, key: string) => (call: Call) => route(key, call) }) as Parameters<typeof mockApi>[0]
  return { routes, rows }
}

const patches = (calls: Call[]) => calls.filter((c) => c.method === 'PATCH')

describe('DraftsPanel', () => {
  it('approves 50 drafts with the keyboard alone, in order, one request each', async () => {
    const user = userEvent.setup()
    const { routes } = backend(Array.from({ length: 50 }, (_, i) => draft(i + 1)))
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(await screen.findAllByTestId('draft-row')).toHaveLength(50)
    for (let i = 0; i < 50; i++) await user.keyboard('a')
    await waitFor(() => expect(patches(calls)).toHaveLength(50))
    expect(patches(calls).map((c) => c.path)).toEqual(Array.from({ length: 50 }, (_, i) => `/drafts/${i + 1}`))
    expect(patches(calls).every((c) => JSON.stringify(c.body) === '{"status":"approved"}')).toBe(true)
    expect(await screen.findByTestId('drafts-empty')).toHaveTextContent('All caught up')
  })

  it('J and K move the selection and R rejects then advances', async () => {
    const user = userEvent.setup()
    const { routes } = backend([draft(1), draft(2), draft(3)])
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await screen.findAllByTestId('draft-row')
    const shown = () => within(screen.getByTestId('draft-detail')).getByRole('heading').textContent
    expect(shown()).toBe('Person 1')
    await user.keyboard('j')
    expect(shown()).toBe('Person 2')
    await user.keyboard('jjj')
    expect(shown()).toBe('Person 3')
    await user.keyboard('kk')
    expect(shown()).toBe('Person 1')
    await user.keyboard('r')
    await waitFor(() => expect(patches(calls)).toHaveLength(1))
    expect(patches(calls)[0]?.body).toEqual({ status: 'rejected' })
    expect(shown()).toBe('Person 2')
  })

  it('ignores shortcuts while typing in a field', async () => {
    const user = userEvent.setup()
    const { routes } = backend([draft(1), draft(2)])
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await screen.findAllByTestId('draft-row')
    await user.click(screen.getByLabelText('Search drafts'))
    await user.keyboard('arj')
    expect(patches(calls)).toHaveLength(0)
    expect(screen.getByLabelText('Search drafts')).toHaveValue('arj')
  })

  it('highlights the model-written opener, shows flags and explains needs_review', async () => {
    const flagged = draft(1, { status: 'needs_review', flags: [{ code: 'opener_fallback', message: 'Failed twice' }, { code: 'guessed_email', message: 'Guessed' }] })
    mockApi(backend([flagged]).routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(await screen.findByTestId('opener-mark')).toHaveTextContent(OPENER)
    expect(screen.getAllByTestId('flag-opener_fallback').length).toBeGreaterThan(0)
    expect(screen.getAllByTestId('flag-guessed_email').length).toBeGreaterThan(0)
    expect(screen.getByText(/never approved in bulk/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve anyway/ })).toBeInTheDocument()
  })

  it('inline edit sends subject and body, and warns that approval is withdrawn', async () => {
    const user = userEvent.setup()
    const { routes } = backend([draft(1, { status: 'approved' })])
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await user.click(await screen.findByRole('button', { name: 'All' }))
    await user.click(await screen.findByRole('button', { name: /Edit/ }))
    expect(screen.getByText(/withdraws the approval/)).toBeInTheDocument()
    const body = screen.getByLabelText('Email body')
    await user.clear(body)
    await user.type(body, 'New text')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(patches(calls)).toHaveLength(1))
    expect(patches(calls)[0]?.body).toEqual({ subject: 'Hello 1', body: 'New text' })
  })

  it('A, R, J and K do nothing while a draft is being edited, even with focus outside the text box', async () => {
    const user = userEvent.setup()
    const { routes } = backend([draft(1), draft(2)])
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await user.click(await screen.findByRole('button', { name: /Edit/ }))
    await user.click(screen.getByRole('heading')) // focus leaves the text box
    expect(screen.getByLabelText('Email body')).not.toHaveFocus()
    await user.keyboard('arjk')
    expect(patches(calls)).toHaveLength(0)
    expect(screen.getByRole('heading')).toHaveTextContent('Person 1')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.keyboard('a')
    await waitFor(() => expect(patches(calls)).toHaveLength(1))
  })

  it('hints at the profile fields when drafts have empty merge fields', async () => {
    mockApi(backend([draft(1, { flags: [{ code: 'missing_field', message: 'Empty merge fields: my_pitch.' }] })]).routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(await screen.findByTestId('missing-hint')).toHaveTextContent('Fill your profile fields')
  })

  it('bulk approve asks first, then calls the API with only_unflagged', async () => {
    const user = userEvent.setup()
    const { routes } = backend([draft(1), draft(2, { flags: [{ code: 'no_context', message: 'x' }] })])
    const { calls } = mockApi(routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await screen.findAllByTestId('draft-row')
    await user.click(screen.getByRole('button', { name: /Approve all unflagged/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('1 draft without flags')
    expect(calls.some((c) => c.path === '/drafts/bulk-approve')).toBe(false)
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/drafts/bulk-approve')?.body).toEqual({ campaign_id: 1, only_unflagged: true, kinds: ['initial'] }))
  })

  it('shows a skeleton, then an empty state with guidance', async () => {
    mockApi(backend([]).routes)
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(screen.getByTestId('drafts-loading')).toBeInTheDocument()
    expect(await screen.findByTestId('drafts-empty')).toHaveTextContent('Generate drafts')
  })

  it('shows a load error with Retry', async () => {
    const user = userEvent.setup()
    let fail = true
    const b = backend([draft(1)])
    mockApi({ 'GET /drafts': (call) => fail ? { status: 500, json: { detail: 'Database is busy' } } : (b.routes['GET /drafts'] as (c: Call) => { json: unknown })(call) })
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Database is busy')
    fail = false
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findAllByTestId('draft-row')).toHaveLength(1)
  })

  it('a failed approve is rolled back and reported', async () => {
    const user = userEvent.setup()
    const b = backend([draft(1), draft(2)])
    mockApi({
      'GET /drafts': b.routes['GET /drafts'] as never,
      'GET /drafts/1': b.routes['GET /drafts/1'] as never, 'GET /drafts/2': b.routes['GET /drafts/2'] as never,
      'PATCH /drafts/1': { status: 409, json: { detail: 'This address is on the suppression list.' } },
    })
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    await screen.findAllByTestId('draft-row')
    await user.keyboard('a')
    await waitFor(() => expect(within(screen.getByTestId('draft-detail')).getByRole('heading')).toHaveTextContent('Person 1'))
    await waitFor(() => expect(screen.queryAllByTestId('status-approved')).toHaveLength(0))
    expect(screen.getAllByTestId('draft-row')).toHaveLength(2)
  })
})
