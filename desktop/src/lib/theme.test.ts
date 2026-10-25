import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyTheme, resolveDark, useTheme } from './theme'

describe('theme', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    localStorage.clear()
    window.reachout = undefined
  })

  it('applies and persists an explicit preference', () => {
    useTheme.getState().setPreference('dark')
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('reachout.theme')).toBe('dark')
    useTheme.getState().setPreference('light')
    expect(document.documentElement).not.toHaveClass('dark')
  })

  it('follows the OS setting for "system"', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
    expect(resolveDark('system')).toBe(true)
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false } as MediaQueryList)
    expect(resolveDark('system')).toBe(false)
  })

  it('tells Electron so the native vibrancy matches', () => {
    const setTheme = vi.fn().mockResolvedValue(undefined)
    window.reachout = { setTheme } as unknown as typeof window.reachout
    applyTheme('dark')
    expect(setTheme).toHaveBeenCalledWith('dark')
  })
})
