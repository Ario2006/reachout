import { readFileSync } from 'node:fs'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Toaster } from '@/components/ui/sonner'
import { InboxPage } from '@/features/inbox/inbox-page'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { mockApi, renderWithProviders, settingsFixture } from '@/test/utils'
import { toast } from 'sonner'
import { AppShell } from './app-shell'
import { Sidebar } from './sidebar'

/** Every button on screen must have an accessible name (icon-only buttons need an aria-label). */
function expectNamedControls(root: HTMLElement) {
  for (const el of within(root).queryAllByRole('button')) {
    expect(el, el.outerHTML.slice(0, 120)).toHaveAccessibleName()
  }
  for (const el of within(root).queryAllByRole('link')) {
    expect(el, el.outerHTML.slice(0, 120)).toHaveAccessibleName()
  }
}

describe('accessibility', () => {
  it('form fields expose their hint and their error through aria-describedby', () => {
    render(
      <Field data-invalid>
        <FieldLabel htmlFor="cap">Daily cap</FieldLabel>
        <Input id="cap" aria-invalid />
        <FieldDescription>Emails per day.</FieldDescription>
        <FieldError>Must be at most 100</FieldError>
      </Field>,
    )
    const input = screen.getByRole('textbox', { name: 'Daily cap' })
    expect(input).toBeInvalid()
    expect(input).toHaveAccessibleDescription('Emails per day. Must be at most 100')
  })

  it('a valid field is described by its hint only', () => {
    render(<Field><FieldLabel htmlFor="n">Name</FieldLabel><Input id="n" /><FieldDescription>Shown in From.</FieldDescription></Field>)
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveAccessibleDescription('Shown in From.')
  })

  it('dialogs take focus and give it back to the trigger on close', async () => {
    const user = userEvent.setup()
    render(
      <Dialog>
        <DialogTrigger asChild><Button>Open it</Button></DialogTrigger>
        <DialogContent>
          <DialogHeader><DialogTitle>Sure?</DialogTitle><DialogDescription>Details</DialogDescription></DialogHeader>
          <Button>Confirm</Button>
        </DialogContent>
      </Dialog>,
    )
    const trigger = screen.getByRole('button', { name: 'Open it' })
    await user.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: 'Sure?' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    await user.tab(); await user.tab(); await user.tab()
    expect(dialog).toContainElement(document.activeElement as HTMLElement) // focus never leaves the dialog
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(trigger).toHaveFocus()
  })

  it('toasts are announced through a polite live region', async () => {
    render(<Toaster />)
    toast('Saved the thing')
    expect(await screen.findByText('Saved the thing')).toBeInTheDocument()
    expect(document.querySelector('section[aria-live="polite"]')).not.toBeNull()
  })

  it('the shell has a skip link and a labelled, focusable content landmark', () => {
    mockApi({ 'GET /setup/status': { json: { checks: [], model: 'm' } }, 'GET /settings': { json: settingsFixture() }, 'GET /sending/status': { json: {} } })
    renderWithProviders(<AppShell />, '/dashboard')
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
    const main = screen.getByRole('main', { name: 'Content' })
    expect(main).toHaveAttribute('tabindex', '-1')
  })

  it('every sidebar control has an accessible name, expanded or collapsed', async () => {
    mockApi({ 'GET /setup/status': { json: { checks: [], model: 'm' } }, 'GET /settings': { json: settingsFixture() }, 'GET /sending/status': { json: { paused_all: false, sent_today: 0, cap: 25 } } })
    renderWithProviders(<Sidebar />)
    expectNamedControls(document.body)
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expectNamedControls(document.body)
    expect(screen.getByRole('link', { name: 'Leads' })).toBeInTheDocument()
    localStorage.removeItem('reachout.sidebar.collapsed')
  })

  it('the inbox has named controls, labelled search and live status', async () => {
    mockApi({
      'GET /threads': { json: { items: [], total: 0, unread_total: 0 } },
      'GET /inbox/status': { json: { checking: false, last_checked_at: null, last_error: null } },
    })
    const { container } = renderWithProviders(<InboxPage />)
    expect(await screen.findByTestId('inbox-empty')).toBeInTheDocument()
    expectNamedControls(container)
    expect(screen.getByRole('textbox', { name: 'Search conversations' })).toBeInTheDocument()
  })

  it('the dashboard chart has a data table for screen readers and a readout for the pointer', async () => {
    mockApi({
      'GET /metrics': { json: {
        kpis: { leads: 1, drafts_awaiting_review: 0, sent_today: 0, daily_cap: 25, sent_total: 0, replies: 0, reply_rate: null, bounce_rate: null, bounced: 0, opted_out: 0, followups_sent: 0, awaiting_followup: 0, delivered: 0 },
        funnel: [{ stage: 'leads', count: 1 }, { stage: 'sent', count: 0 }],
        per_day: [{ day: '2026-10-26', sent: 10, replies: 1, bounces: 0 }, { day: '2026-10-27', sent: 20, replies: 3, bounces: 1 }],
        attention: [], opens_note: 'Opens are not tracked.',
      } },
      'GET /activity': { json: [] },
    })
    const { container } = renderWithProviders(<DashboardPage />)
    const table = await screen.findByRole('table', { name: 'Emails sent per day' })
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(within(table).getByRole('columnheader', { name: 'Bounces' })).toBeInTheDocument()
    expectNamedControls(container)
  })

  it('reduced motion switches animations and transitions off', () => {
    const css = readFileSync('src/index.css', 'utf8')
    const block = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'))
    expect(block).toMatch(/animation-duration:\s*0\.01ms\s*!important/)
    expect(block).toMatch(/transition-duration:\s*0\.01ms\s*!important/)
  })
})
