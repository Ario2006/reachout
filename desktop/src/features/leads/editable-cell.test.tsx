import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EditableCell } from './editable-cell'

function setup(value = 'Acme') {
  const onSave = vi.fn().mockResolvedValue(undefined)
  render(<EditableCell label="Company" value={value} onSave={onSave} />)
  return { onSave, cell: () => screen.getByRole('button', { name: /^Company:/ }) }
}

describe('EditableCell', () => {
  it('shows an accessible, keyboard-reachable value', () => {
    const { cell } = setup()
    expect(cell()).toHaveAttribute('tabindex', '0')
    expect(cell()).toHaveTextContent('Acme')
    expect(cell().getAttribute('aria-label')).toMatch(/Double-click or press Enter to edit/)
  })

  it('shows a dash for an empty value but still announces it as empty', () => {
    setup('')
    expect(screen.getByRole('button', { name: /Company: empty/ })).toHaveTextContent('—')
  })

  it('edits on double-click and saves on Enter, trimmed', async () => {
    const { cell, onSave } = setup()
    await userEvent.dblClick(cell())
    const input = screen.getByRole('textbox', { name: 'Edit Company' })
    await userEvent.clear(input)
    await userEvent.type(input, '  Beta Labs {Enter}')
    expect(onSave).toHaveBeenCalledExactlyOnceWith('Beta Labs')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('opens with Enter on the focused cell', async () => {
    const { cell } = setup()
    cell().focus()
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('textbox', { name: 'Edit Company' })).toHaveValue('Acme')
  })

  it('Escape cancels without saving, even though the input then loses focus', async () => {
    const { cell, onSave } = setup()
    await userEvent.dblClick(cell())
    await userEvent.type(screen.getByRole('textbox'), 'zzz{Escape}')
    expect(onSave).not.toHaveBeenCalled()
    expect(cell()).toHaveTextContent('Acme')
  })

  it('saves on blur', async () => {
    const { cell, onSave } = setup()
    await userEvent.dblClick(cell())
    await userEvent.type(screen.getByRole('textbox'), 'X')
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledWith('AcmeX')
  })

  it('does not call save when nothing changed', async () => {
    const { cell, onSave } = setup()
    await userEvent.dblClick(cell())
    await userEvent.keyboard('{Enter}')
    expect(onSave).not.toHaveBeenCalled()
  })
})
