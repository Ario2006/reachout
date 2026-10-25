import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderWithProviders } from '@/test/utils'
import { GmailForm } from './gmail-form'

const ok = (id: string) => ({ id, name: id.toUpperCase(), status: 'ok', detail: 'Logged in', hint: '' })

describe('GmailForm', () => {
  it('rejects an invalid address without calling the API', async () => {
    const { calls } = mockApi({})
    renderWithProviders(<GmailForm address="" passwordSet={false} />)
    await userEvent.type(screen.getByLabelText('Gmail address'), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Enter a valid email address')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('sends the password once, then clears the field', async () => {
    const { calls } = mockApi({
      'PUT /settings/gmail': { json: { address: 'me@gmail.com', password_set: true } },
      'GET /settings': { json: {} }, 'GET /setup/status': { json: {} },
    })
    renderWithProviders(<GmailForm address="" passwordSet={false} />)
    await userEvent.type(screen.getByLabelText('Gmail address'), 'me@gmail.com')
    await userEvent.type(screen.getByLabelText('App Password'), 'abcd efgh ijkl mnop')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ address: 'me@gmail.com', app_password: 'abcd efgh ijkl mnop' })
    await waitFor(() => expect(screen.getByLabelText('App Password')).toHaveValue(''))
  })

  it('sends null (keep existing) when the password field is blank', async () => {
    const { calls } = mockApi({
      'PUT /settings/gmail': { json: { address: 'me@gmail.com', password_set: true } },
      'GET /settings': { json: {} }, 'GET /setup/status': { json: {} },
    })
    renderWithProviders(<GmailForm address="old@gmail.com" passwordSet />)
    const addr = screen.getByLabelText('Gmail address')
    await userEvent.clear(addr)
    await userEvent.type(addr, 'me@gmail.com')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ address: 'me@gmail.com', app_password: null }))
  })

  it('offers removal only when a password is saved, and Test only for a saved, unedited form', async () => {
    mockApi({})
    const { unmount } = renderWithProviders(<GmailForm address="" passwordSet={false} />)
    expect(screen.queryByRole('button', { name: 'Remove saved password' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled()
    unmount()
    renderWithProviders(<GmailForm address="me@gmail.com" passwordSet />)
    expect(screen.getByRole('button', { name: 'Remove saved password' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled()
  })

  it('shows SMTP and IMAP results from the connection test', async () => {
    mockApi({ 'POST /setup/test-gmail': { json: { smtp: ok('smtp'), imap: { ...ok('imap'), status: 'fail', detail: 'Gmail rejected the login.', hint: 'Enable IMAP' } } } })
    renderWithProviders(<GmailForm address="me@gmail.com" passwordSet />)
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByTestId('check-smtp')).toHaveTextContent('Logged in')
    expect(screen.getByTestId('check-imap')).toHaveTextContent('Gmail rejected the login.')
    expect(screen.getByTestId('check-imap')).toHaveTextContent('Enable IMAP')
  })
})
