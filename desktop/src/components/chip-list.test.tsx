import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ChipList } from './chip-list'

function Harness({ initial = ['founder'], max }: { initial?: string[]; max?: number }) {
  const [items, setItems] = useState(initial)
  return <ChipList label="Roles" items={items} onChange={setItems} max={max ?? 12} />
}

describe('ChipList', () => {
  it('adds on Enter and on comma, trimming whitespace', async () => {
    render(<Harness />)
    const input = screen.getByRole('textbox', { name: /Roles/ })
    await userEvent.type(input, '  CTO {Enter}')
    await userEvent.type(input, 'partner,')
    const list = screen.getByRole('list', { name: 'Roles' })
    expect(list).toHaveTextContent('founder')
    expect(list).toHaveTextContent('CTO')
    expect(list).toHaveTextContent('partner')
    expect(input).toHaveValue('')
  })

  it('ignores duplicates case-insensitively and blank input', async () => {
    render(<Harness />)
    const input = screen.getByRole('textbox', { name: /Roles/ })
    await userEvent.type(input, 'FOUNDER{Enter}   {Enter}')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('removes with the labelled button and with Backspace on an empty input', async () => {
    render(<Harness initial={['a', 'b', 'c']} />)
    await userEvent.click(screen.getByRole('button', { name: 'Remove b' }))
    expect(screen.getAllByRole('listitem').map((e) => e.textContent)).toEqual(['a', 'c'])
    await userEvent.type(screen.getByRole('textbox', { name: /Roles/ }), '{Backspace}')
    expect(screen.getAllByRole('listitem').map((e) => e.textContent)).toEqual(['a'])
  })

  it('stops accepting input at the maximum and says how many are used', async () => {
    render(<Harness initial={['a', 'b']} max={2} />)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('(2/2)')).toBeInTheDocument()
  })

  it('commits a half-typed value on blur', async () => {
    render(<Harness />)
    await userEvent.type(screen.getByRole('textbox', { name: /Roles/ }), 'associate')
    await userEvent.tab()
    expect(screen.getByRole('list', { name: 'Roles' })).toHaveTextContent('associate')
  })
})
