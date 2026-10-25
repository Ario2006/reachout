import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { SetupStatus } from '@/lib/api/types'
import { useLive } from '@/lib/jobs-store'
import { mockApi, renderWithProviders } from '@/test/utils'
import { SetupWizard } from './setup-wizard'

const chk = (id: string, status: string, detail = 'x', hint = '') => ({ id, name: id, status, detail, hint })

function status(over: Partial<SetupStatus> = {}, checks: Record<string, string> = {}): SetupStatus {
  const c = { data_dir: 'ok', ollama: 'ok', model: 'ok', memory: 'ok', ...checks }
  return {
    first_run_complete: false, legal_acknowledged: false, display_name: '', footer_text: "Reply 'stop' to opt out.",
    model: 'qwen2.5:3b-instruct',
    checks: Object.entries(c).map(([id, s]) => chk(id, s, id === 'ollama' && s !== 'ok' ? 'Ollama is not installed.' : 'fine', s !== 'ok' ? 'Run: brew install ollama' : '')),
    gmail: { address: '', password_set: false }, worker: { alive: true }, pull_job: null, ...over,
  } as SetupStatus
}

describe('SetupWizard', () => {
  beforeEach(() => useLive.setState({ jobs: {} }))

  it('starts on Ollama and blocks Continue until it is running', () => {
    const s = status({}, { ollama: 'fail', model: 'fail' })
    mockApi({ 'GET /setup/status': { json: s } })
    renderWithProviders(<SetupWizard initial={s} />)
    expect(screen.getByRole('heading', { name: 'Ollama' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
    expect(screen.getByText('Run: brew install ollama')).toBeInTheDocument()
  })

  it('jumps to the first incomplete step', () => {
    const s = status({}, { model: 'fail' })
    mockApi({ 'GET /setup/status': { json: s } })
    renderWithProviders(<SetupWizard initial={s} />)
    expect(screen.getByRole('heading', { name: 'Language model' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
  })

  it('queues a model download and shows live progress from events', async () => {
    const s = status({}, { model: 'fail' })
    const { calls } = mockApi({
      'GET /setup/status': { json: s },
      'POST /setup/pull-model': { status: 202, json: { id: 4, kind: 'pull_model', state: 'queued', progress: 0, total: 0, payload: {}, cancel_requested: false, created_at: 'x' } },
    })
    renderWithProviders(<SetupWizard initial={s} />)
    await userEvent.click(screen.getByRole('button', { name: /Download qwen2.5:3b-instruct/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    useLive.getState().upsertJob({ id: 4, kind: 'pull_model', state: 'running', progress: 55, total: 100, message: 'pulling layer', error: null })
    expect(await screen.findByText('pulling layer')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Download progress' })).toHaveAttribute('aria-valuenow', '55')
  })

  it('lets Gmail be skipped, requires name + legal acknowledgement, then completes', async () => {
    const s = status()
    const { calls } = mockApi({
      'GET /setup/status': { json: s },
      'POST /setup/complete': { json: { ...s, first_run_complete: true } },
    })
    renderWithProviders(<SetupWizard initial={s} />)
    expect(screen.getByRole('heading', { name: 'Connect Gmail' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }))

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Enter your real name')).toBeInTheDocument()
    expect(await screen.findByText('Please confirm to continue')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Your name'), 'Aryan Kumar')
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('You are all set')).toBeInTheDocument()
    expect(screen.getByText('not connected yet')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Open Reachout' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/setup/complete')).toBe(true))
    expect(calls.find((c) => c.path === '/setup/complete')?.body).toEqual({
      display_name: 'Aryan Kumar', footer_text: "Reply 'stop' to opt out.", legal_acknowledged: true,
    })
  })
})
