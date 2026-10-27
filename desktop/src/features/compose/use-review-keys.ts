import { useEffect } from 'react'

export interface ReviewKeys {
  enabled: boolean
  onApprove: () => void
  onReject: () => void
  onNext: () => void
  onPrev: () => void
}

function isTyping(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.getAttribute('role') === 'combobox'
}

/** A approve, R reject, J next, K previous. Ignored while typing, with a modifier key, or while a dialog is open. */
export function useReviewKeys({ enabled, onApprove, onReject, onNext, onPrev }: ReviewKeys): void {
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || isTyping(e.target)) return
      if (document.querySelector('[role="dialog"]')) return
      const act = { a: onApprove, r: onReject, j: onNext, k: onPrev }[e.key.toLowerCase()]
      if (!act) return
      e.preventDefault()
      act()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled, onApprove, onReject, onNext, onPrev])
}
