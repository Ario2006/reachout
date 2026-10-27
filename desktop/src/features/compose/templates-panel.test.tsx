import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Template } from '@/lib/api/types'
import { mockApi, renderWithProviders, type Call } from '@/test/utils'
import { TemplatesPanel } from './templates-panel'

const TPL: Template = {
  id: 1, mode: 'internship', name: 'Internship', subject: 'Internship at {{company}}?', is_starter: true, updated_at: 'x',
  body: 'Hi {{first_name}},\n\n{{custom_opener}}\n\nThanks,\n{{my_name}}', followup_1_body: null, followup_2_body: null,
  merge_fields: ['first_name', 'company', 'my_name', 'custom_opener'],
}
const PROFILE = { fields: [{ key: 'my_summary', label: 'About me', value: '' }], reserved: ['first_name', 'company', 'role', 'my_name', 'custom_opener'] }
const previewFor = (call: Call) => {
  const t = (call.body as { template: { body: string; subject: string } }).template
  const body = t.body.replace('{{custom_opener}}', '(sample opener)').replace('{{first_name}}', 'Alex').replace('{{my_name}}', 'Sam')
  if (t.body.includes('{% broken')) return { status: 422, json: { detail: 'Template error on line 1: unexpected end' } }
  const at = body.indexOf('(sample opener)')
  return { json: { subject: t.subject, body, final_text: body + '\n\n-- \nSam\nreply stop', opener_span: at < 0 ? null : { start: at, end: at + 15 }, missing_fields: [], word_count: body.split(/\s+/).length } }
}
const routes = () => ({
  'GET /templates': { json: [TPL] }, 'GET /profile': { json: PROFILE }, 'GET /leads': { json: { items: [], total: 0 } },
  'POST /templates/preview': previewFor,
})

describe('TemplatesPanel', () => {
  it('lists templates and shows a live preview with the model-written sentence highlighted', async () => {
    mockApi(routes())
    renderWithProviders(<TemplatesPanel />)
    expect(await screen.findByDisplayValue('Internship')).toBeInTheDocument()
    expect(await screen.findByTestId('opener-mark')).toHaveTextContent('(sample opener)')
    expect(screen.getByTestId('preview-subject')).toHaveTextContent('Internship at {{company}}?')
  })

  it('merge-field chips insert at the cursor and the preview follows the edit', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi(routes())
    renderWithProviders(<TemplatesPanel />)
    const body = await screen.findByLabelText('Body')
    await user.clear(body)
    await user.type(body, 'Hello ')
    await user.click(screen.getByRole('button', { name: 'Insert my_summary' }))
    await waitFor(() => expect(body).toHaveFocus())
    await user.keyboard(' and {{{{custom_opener}}')
    expect(body).toHaveValue('Hello {{my_summary}} and {{custom_opener}}')
    await waitFor(() => {
      const last = [...calls].reverse().find((c) => c.path === '/templates/preview')
      expect((last?.body as { template: { body: string } }).template.body).toBe('Hello {{my_summary}} and {{custom_opener}}')
    })
  })

  it('reports an unknown merge field and a template syntax error inline', async () => {
    const user = userEvent.setup()
    mockApi(routes())
    renderWithProviders(<TemplatesPanel />)
    const body = await screen.findByLabelText('Body')
    await user.clear(body)
    await user.type(body, '{{{{unknown_thing}}')
    expect(await screen.findByRole('status')).toHaveTextContent('{{unknown_thing}}')
    await user.clear(body)
    await user.type(body, '{{% broken')
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/line 1/)).toBeInTheDocument()
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('saving sends the edited template to PUT', async () => {
    const user = userEvent.setup()
    const { calls } = mockApi({ ...routes(), 'PUT /templates/1': (c: Call) => ({ json: { ...TPL, ...(c.body as object) } }) })
    renderWithProviders(<TemplatesPanel />)
    const name = await screen.findByLabelText('Name')
    await user.type(name, ' v2')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ name: 'Internship v2', mode: 'internship' })
  })
})
