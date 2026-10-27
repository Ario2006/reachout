import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Toaster } from '@/components/ui/sonner'
import { mockApi, renderWithProviders } from '@/test/utils'
import { CommandPalette } from './command-palette'
import { NAV } from './nav'

const STATUS = { paused_all: false, sent_today: 0, cap: 25, in_flight: 0, next_send_at: null, worker_alive: true, caffeinate_active: false }

function Where() {
  return <div data-testid="where">{useLocation().pathname}{useLocation().search}</div>
}

function setup(extra: Record<string, unknown> = {}) {
  const api = mockApi({
    'GET /sending/status': { json: STATUS },
    'GET /leads': { json: { items: [{ id: 4, name: 'Ada Lovelace', email: 'ada@engine.io', company: 'Engine' }], total: 1 } },
    ...extra,
  })
  renderWithProviders(<><Routes><Route path="*" element={<Where />} /></Routes><CommandPalette /><Toaster /></>)
  return api
}

async function openPalette(user: ReturnType<typeof userEvent.setup>) {
  await user.keyboard('{Control>}k{/Control}')
  return screen.findByRole('dialog')
}

describe('command palette', () => {
  it('offers every screen with its Cmd+number shortcut', async () => {
    setup()
    await openPalette(userEvent.setup())
    for (const [i, n] of NAV.entries()) {
      expect(screen.getByRole('option', { name: new RegExp(`${n.label}.*⌘${i + 1}`) })).toBeInTheDocument()
    }
  })

  it('jumps to a screen and closes', async () => {
    const user = userEvent.setup()
    setup()
    await openPalette(user)
    await user.click(screen.getByRole('option', { name: /Inbox.*⌘6/ }))
    expect(screen.getByTestId('where')).toHaveTextContent('/inbox')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('"New search" opens Discover', async () => {
    const user = userEvent.setup()
    setup()
    await openPalette(user)
    await user.click(screen.getByRole('option', { name: 'New search' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/discover')
  })

  it('"Check inbox now" posts to the API and toasts', async () => {
    const user = userEvent.setup()
    const { calls } = setup({ 'POST /inbox/check-now': { status: 202, json: { id: 1 } }, 'GET /inbox/status': { json: {} }, 'GET /threads': { json: { items: [], total: 0, unread_total: 0 } } })
    await openPalette(user)
    await user.click(screen.getByRole('option', { name: 'Check inbox now' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/inbox/check-now')).toBe(true))
    expect(await screen.findByText('Checking Gmail for replies and bounces')).toBeInTheDocument()
  })

  it('"Pause sending" posts pause-all and toasts the result; Resume is offered once paused', async () => {
    const user = userEvent.setup()
    const { calls } = setup({ 'POST /sending/pause-all': { json: { ...STATUS, paused_all: true } } })
    await openPalette(user)
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Resume sending' })).toBeNull())
    await user.click(await screen.findByRole('option', { name: 'Pause sending' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/sending/pause-all')).toBe(true))
    expect(await screen.findByText('Sending paused for every campaign')).toBeInTheDocument()
    await openPalette(user)
    expect(await screen.findByRole('option', { name: 'Resume sending' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Pause sending' })).toBeNull()
  })

  it('searches leads by name, email or company', async () => {
    const user = userEvent.setup()
    const { calls } = setup()
    await openPalette(user)
    await user.type(screen.getByRole('combobox'), 'lovelace')
    await user.click(await screen.findByRole('option', { name: /Ada Lovelace/ }))
    expect(calls.some((c) => c.path.startsWith('/leads') && c.path.includes('q=lovelace'))).toBe(true)
    expect(screen.getByTestId('where')).toHaveTextContent('/leads?q=ada%40engine.io')
  })
})
