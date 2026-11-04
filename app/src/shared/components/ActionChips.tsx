/**
 * Action chips (§9.2.2). Hidden without a selection except `Do…`. Arrow keys move between chips.
 */
import type { KeyboardEvent } from 'react'
import type { RunAction } from '../protocol'

const CHIPS: { action: RunAction; label: string; needsSelection: boolean }[] = [
  { action: 'summarise', label: 'Summarise', needsSelection: true },
  { action: 'explain', label: 'Explain', needsSelection: true },
  { action: 'translate', label: 'Translate', needsSelection: true },
  { action: 'rewrite', label: 'Rewrite', needsSelection: true },
  { action: 'fix', label: 'Fix', needsSelection: true },
  { action: 'do', label: 'Do…', needsSelection: false },
]

interface ActionChipsProps {
  hasSelection: boolean
  onAction: (action: RunAction) => void
  disabled?: boolean
}

function moveFocus(e: KeyboardEvent<HTMLDivElement>): void {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
  const buttons = [...e.currentTarget.querySelectorAll('button')]
  const i = buttons.indexOf(document.activeElement as HTMLButtonElement)
  if (i < 0) return
  e.preventDefault()
  const next = (i + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  buttons[next]?.focus()
}

export function ActionChips({ hasSelection, onAction, disabled = false }: ActionChipsProps) {
  const chips = CHIPS.filter((c) => hasSelection || !c.needsSelection)
  return (
    <div className="flex flex-wrap gap-1.5" role="toolbar" onKeyDown={moveFocus}>
      {chips.map((c) => (
        <button
          key={c.action}
          type="button"
          onClick={() => onAction(c.action)}
          disabled={disabled && c.action !== 'do'}
          className="rounded-full border border-ck-border bg-white/5 px-2.5 py-0.5 text-xs text-ck-text outline-none hover:border-ck-violet/60 focus-visible:border-ck-violet disabled:cursor-default disabled:opacity-50"
        >
          {c.label}
        </button>
      ))}
    </div>
  )
}
