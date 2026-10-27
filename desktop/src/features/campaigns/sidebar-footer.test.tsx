import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Sidebar } from '@/app/sidebar'
import { mockApi, renderWithProviders, settingsFixture } from '@/test/utils'

const STATUS = { paused_all: false, sent_today: 7, cap: 25, in_flight: 0, next_send_at: null, worker_alive: true, caffeinate_active: true }

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /setup/status': { json: { checks: [], model: 'm' } },
    'GET /settings': { json: settingsFixture() },
    'GET /sending/status': { json: STATUS },
    ...extra,
  }
}

describe('sidebar footer', () => {
  it('shows today\'s sends against the cap and the awake note', async () => {
    mockApi(routes())
    renderWithProviders(<Sidebar />)
    await waitFor(() => expect(screen.getByTestId('sends-today')).toHaveTextContent('7 / 25'))
    expect(await screen.findByTestId('awake-note')).toBeInTheDocument()
  })

  it('flips the pause-all switch through the API', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi(routes({ 'POST /sending/pause-all': { json: { ...STATUS, paused_all: true } } }))
    renderWithProviders(<Sidebar />)
    const sw = await screen.findByRole('switch', { name: 'Pause all sending' })
    await waitFor(() => expect(sw).toBeEnabled())
    await user.click(sw)
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/sending/pause-all')).toBe(true))
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Pause all sending' })).toBeChecked())
  })
})
