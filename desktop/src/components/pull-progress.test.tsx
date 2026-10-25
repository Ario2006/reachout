import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { useLive, type JobEvent } from '@/lib/jobs-store'
import { mockApi, renderWithProviders } from '@/test/utils'
import { PullProgress } from './pull-progress'

const job = (over: Partial<JobEvent> = {}): JobEvent => ({
  id: 3, kind: 'pull_model', state: 'running', progress: 40, total: 100, message: 'pulling abc', error: null, ...over,
})

describe('PullProgress', () => {
  beforeEach(() => useLive.setState({ jobs: {} }))

  it('shows status text and percentage while running', () => {
    mockApi({})
    useLive.getState().upsertJob(job())
    renderWithProviders(<PullProgress jobId={3} />)
    expect(screen.getByText('pulling abc')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Download progress' })).toHaveAttribute('aria-valuenow', '40')
  })

  it('explains a queued job instead of showing a bare spinner', () => {
    mockApi({})
    useLive.getState().upsertJob(job({ state: 'queued', progress: 0, message: null }))
    renderWithProviders(<PullProgress jobId={3} />)
    expect(screen.getByText(/Waiting for the worker/)).toBeInTheDocument()
  })

  it('cancels through the API', async () => {
    const { calls } = mockApi({ 'POST /jobs/3/cancel': { json: {} } })
    useLive.getState().upsertJob(job())
    renderWithProviders(<PullProgress jobId={3} />)
    await userEvent.click(screen.getByRole('button', { name: /Cancel/ }))
    expect(calls.some((c) => c.method === 'POST' && c.path === '/jobs/3/cancel')).toBe(true)
  })

  it('shows the failure reason and no cancel button once failed', () => {
    mockApi({})
    useLive.getState().upsertJob(job({ state: 'failed', error: 'manifest does not exist' }))
    renderWithProviders(<PullProgress jobId={3} />)
    expect(screen.getByRole('alert')).toHaveTextContent('manifest does not exist')
    expect(screen.queryByRole('button', { name: /Cancel/ })).toBeNull()
  })

  it('uses the seed until a live event arrives', () => {
    mockApi({})
    renderWithProviders(<PullProgress jobId={3} seed={job({ progress: 10, message: 'seeded' })} />)
    expect(screen.getByText('seeded')).toBeInTheDocument()
  })
})
