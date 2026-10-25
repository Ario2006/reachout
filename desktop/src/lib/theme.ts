import type { ThemePreference } from '@shared/bridge'
import { create } from 'zustand'

const KEY = 'reachout.theme'

function read(): ThemePreference {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    // storage unavailable
  }
  return 'system'
}

export function resolveDark(pref: ThemePreference): boolean {
  if (pref === 'dark') return true
  if (pref === 'light') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** Apply the preference to <html> and tell Electron so the native vibrancy matches. */
export function applyTheme(pref: ThemePreference): void {
  document.documentElement.classList.toggle('dark', resolveDark(pref))
  void window.reachout?.setTheme(pref)
}

interface ThemeState {
  preference: ThemePreference
  setPreference: (p: ThemePreference) => void
}

export const useTheme = create<ThemeState>((set) => ({
  preference: read(),
  setPreference: (preference) => {
    try {
      localStorage.setItem(KEY, preference)
    } catch {
      // ignore
    }
    applyTheme(preference)
    set({ preference })
  },
}))

/** Call once at startup: apply the stored preference and follow OS changes while on "system". */
export function initTheme(): void {
  applyTheme(useTheme.getState().preference)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useTheme.getState().preference === 'system') applyTheme('system')
  })
}
