/** Result actions (§9.2.5): Copy · Replace selection · Insert · Open in assistant. */
import { CopyButton } from './CopyButton'

const buttonClass =
  'rounded-md border border-ck-border bg-black/40 px-2 py-0.5 text-xs text-ck-text outline-none hover:border-ck-violet/60 focus-visible:border-ck-violet disabled:opacity-50'

interface ResultActionsProps {
  text: string
  /** The captured app had a selection that can be replaced (not a terminal, selection kept). */
  canReplace: boolean
  /** There is a captured window to paste into. */
  canInsert: boolean
  busy: boolean
  onReplace: () => void
  onInsert: () => void
  onOpenInAssistant: () => void
}

export function ResultActions({
  text,
  canReplace,
  canInsert,
  busy,
  onReplace,
  onInsert,
  onOpenInAssistant,
}: ResultActionsProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <CopyButton getText={() => text} />
      {canReplace && (
        <button
          type="button"
          onClick={onReplace}
          disabled={busy}
          title="Replace the selection (Ctrl+Enter)"
          className={buttonClass}
        >
          Replace selection
        </button>
      )}
      {canInsert && (
        <button
          type="button"
          onClick={onInsert}
          disabled={busy}
          title="Insert at the cursor"
          className={buttonClass}
        >
          Insert
        </button>
      )}
      <button
        type="button"
        onClick={onOpenInAssistant}
        disabled={busy}
        title="Move this answer to the assistant workspace"
        className={buttonClass}
      >
        Open in workspace
      </button>
    </div>
  )
}
