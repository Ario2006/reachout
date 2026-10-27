import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, renderWithProviders } from '@/test/utils'
import { ImportDialog } from './import-dialog'

const REPORT = { total: 5, valid: 3, inserted: 0, duplicates: 1, suppressed: 0, invalid: 1, no_mx: 0, issues: [{ row: 3, email: 'nope', reason: 'Invalid or unusable email address' }] }
const csv = (text = 'email\na@good.ai\n') => new File([text], 'leads.csv', { type: 'text/csv' })

describe('ImportDialog', () => {
  it('previews with a dry run, then imports only after confirmation', async () => {
    const onOpenChange = vi.fn()
    const { calls } = mockApi({
      'POST /leads/import-csv': (c) => ({ json: c.path.includes('dry_run=true') ? REPORT : { ...REPORT, inserted: 3 } }),
      'GET /leads': { json: { items: [], total: 0 } },
    })
    renderWithProviders(<ImportDialog open onOpenChange={onOpenChange} />)
    const importBtn = screen.getByRole('button', { name: /^Import/ })
    expect(importBtn).toBeDisabled()

    await userEvent.upload(document.querySelector<HTMLInputElement>('#csv-file') as HTMLInputElement, csv())
    const report = await screen.findByTestId('import-report')
    expect(report).toHaveTextContent('3 leads ready to import from 5 rows')
    expect(report).toHaveTextContent('1 invalid address(es)')
    expect(calls.filter((c) => c.path.startsWith('/leads/import-csv'))).toHaveLength(1)
    expect(calls[0]?.path).toContain('dry_run=true')

    await userEvent.click(screen.getByText(/Skipped rows/))
    expect(screen.getByText(/row 3: nope/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Import 3 leads' }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    const real = calls.filter((c) => c.path.startsWith('/leads/import-csv'))[1]
    expect(real?.path).toContain('dry_run=false')
  })

  it('sends the file content as raw CSV text with the right content type', async () => {
    const { calls } = mockApi({ 'POST /leads/import-csv': { json: REPORT } })
    renderWithProviders(<ImportDialog open onOpenChange={() => {}} />)
    await userEvent.upload(document.querySelector<HTMLInputElement>('#csv-file') as HTMLInputElement, csv('email,name\nz@good.ai,Zed\n'))
    await screen.findByTestId('import-report')
    expect(calls[0]?.headers.get('Content-Type')).toBe('text/csv')
    expect(calls[0]?.body).toBe('email,name\nz@good.ai,Zed\n') // sent verbatim, not JSON-encoded
  })

  it('shows the server error and keeps Import disabled when the file is unusable', async () => {
    mockApi({ 'POST /leads/import-csv': { status: 422, json: { detail: "The CSV needs an 'email' column." } } })
    renderWithProviders(<ImportDialog open onOpenChange={() => {}} />)
    await userEvent.upload(document.querySelector<HTMLInputElement>('#csv-file') as HTMLInputElement, csv('name\nx\n'))
    expect(await screen.findByRole('alert')).toHaveTextContent("The CSV needs an 'email' column.")
    expect(screen.getByRole('button', { name: /^Import/ })).toBeDisabled()
  })

  it('cannot import when no row is valid', async () => {
    mockApi({ 'POST /leads/import-csv': { json: { ...REPORT, valid: 0 } } })
    renderWithProviders(<ImportDialog open onOpenChange={() => {}} />)
    await userEvent.upload(document.querySelector<HTMLInputElement>('#csv-file') as HTMLInputElement, csv())
    await screen.findByTestId('import-report')
    expect(screen.getByRole('button', { name: /^Import/ })).toBeDisabled()
  })
})
