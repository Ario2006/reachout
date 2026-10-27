import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderWithProviders } from '@/test/utils'
import { ComposePage } from './compose-page'

describe('ComposePage', () => {
  it('guides the user to create a campaign when there is none', async () => {
    mockApi({ 'GET /campaigns': { json: [] }, 'GET /templates': { json: [] } })
    renderWithProviders(<ComposePage />, '/compose')
    expect(await screen.findByTestId('no-campaign')).toHaveTextContent('Create a campaign')
    expect(screen.getByRole('button', { name: /New campaign/ })).toBeInTheDocument()
  })

  it('shows a campaign load error with Retry', async () => {
    mockApi({ 'GET /campaigns': { status: 500, json: { detail: 'Backend hiccup' } }, 'GET /templates': { json: [] } })
    renderWithProviders(<ComposePage />, '/compose')
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend hiccup')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
