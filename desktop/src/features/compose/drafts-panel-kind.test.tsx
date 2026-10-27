import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Campaign, DraftOut } from '@/lib/api/types'
import { mockApi, renderWithProviders } from '@/test/utils'
import { DraftsPanel } from './drafts-panel'

const CAMPAIGN = { id: 1, name: 'Interns', template_id: 3, state: 'draft' } as Campaign
const draft = (id: number, kind: DraftOut['kind']): DraftOut => ({
  id, campaign_id: 1, kind, subject: `Subject ${id}`, body: 'Hi', opener: null, opener_source: 'none', opener_span: null, flags: [], status: 'pending', updated_at: '2026-10-26 10:00:00',
  lead: { id, name: `Person ${id}`, email: `p${id}@acme.ai`, role: null, company: 'Acme', email_confidence: 'published_personal', context_snippet: null, source_url: null },
} as unknown as DraftOut)
const page = (items: DraftOut[]) => ({ items, total: items.length, counts: { pending: items.length, needs_review: 0, approved: 0, rejected: 0 } })

describe('drafts panel kind filter', () => {
  it('shows initial drafts by default and merges follow-up 1 and 2 under Follow-ups', async () => {
    const { calls } = mockApi({
      'GET /drafts': (call) => {
        const kind = new URL('http://x' + call.path).searchParams.get('kind')
        return { json: page(kind === 'initial' ? [draft(1, 'initial')] : kind === 'followup_1' ? [draft(2, 'followup_1')] : [draft(3, 'followup_2')]) }
      },
      'GET /drafts/1': { json: { ...draft(1, 'initial'), final_text: 'x' } },
      'GET /drafts/2': { json: { ...draft(2, 'followup_1'), final_text: 'x' } },
      'GET /drafts/3': { json: { ...draft(3, 'followup_2'), final_text: 'x' } },
    })
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />)
    expect(await screen.findAllByTestId('draft-row')).toHaveLength(1)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Follow-ups' }))
    await waitFor(() => expect(screen.getAllByTestId('draft-row')).toHaveLength(2))
    expect(screen.getByText(/Follow-up 1 · Acme/)).toBeInTheDocument()
    expect(screen.getByText(/Follow-up 2 · Acme/)).toBeInTheDocument()
    const kinds = calls.filter((c) => c.path.startsWith('/drafts?')).map((c) => new URL('http://x' + c.path).searchParams.get('kind'))
    expect(kinds).toEqual(expect.arrayContaining(['initial', 'followup_1', 'followup_2']))
  })

  it('opens on Follow-ups when the URL says kind=followups', async () => {
    mockApi({ 'GET /drafts': { json: page([]) } })
    renderWithProviders(<DraftsPanel campaign={CAMPAIGN} />, '/compose?campaign=1&kind=followups')
    expect(await screen.findByRole('button', { name: 'Follow-ups' })).toHaveAttribute('aria-pressed', 'true')
  })
})
