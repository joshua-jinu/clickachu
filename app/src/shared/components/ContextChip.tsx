/**
 * Context chip (§9.2.1): app · project · "N lines selected". Click to view the captured text,
 * × to drop it from what is sent to the model. The preview is a separate component so the host
 * can place it below the chip row.
 */
import type { ContextSnapshot } from '../protocol'

export interface ContextChipProps {
  context: ContextSnapshot | null
  /** The user removed the selection from what gets sent. */
  dropped: boolean
  expanded: boolean
  onToggle: () => void
  onDrop: () => void
}

const lineCount = (text: string): number => text.split(/\r?\n/).length

export function ContextChip({ context, dropped, expanded, onToggle, onDrop }: ContextChipProps) {
  const selection = context?.selection ?? null
  const lines = selection ? lineCount(selection) : 0
  const selectionLabel = dropped
    ? 'selection removed'
    : lines
      ? `${lines} line${lines === 1 ? '' : 's'} selected`
      : 'no selected text captured'
  const label = [context?.app, context?.project?.name, selectionLabel].filter(Boolean).join(' · ')
  const chipClass =
    'inline-flex max-w-full items-center gap-1.5 rounded-full border border-ck-border bg-white/5 px-2.5 py-0.5 text-xs text-ck-muted'

  const body = (
    <>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-ck-violet/70" />
      <span className="truncate">{label || 'No selection'}</span>
    </>
  )

  if (!selection) return <div className={chipClass}>{body}</div>

  return (
    <div className={chipClass}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        title="View captured text"
        className="inline-flex min-w-0 items-center gap-1.5 outline-none hover:text-ck-text focus-visible:text-ck-text"
      >
        {body}
      </button>
      {!dropped && (
        <button
          type="button"
          onClick={onDrop}
          aria-label="Don't send the selection"
          title="Don't send the selection"
          className="shrink-0 leading-none outline-none hover:text-ck-text focus-visible:text-ck-text"
        >
          ×
        </button>
      )}
    </div>
  )
}

const METHOD_LABEL = { uia: 'UI Automation', clipboard: 'clipboard', none: 'none' } as const

export function SelectionPreview({ context }: { context: ContextSnapshot }) {
  if (!context.selection) return null
  return (
    <div className="shrink-0 rounded-lg border border-ck-border bg-black/30 p-2">
      <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-ck-text">
        {context.selection}
      </pre>
      <p className="mt-1 text-[11px] text-ck-muted">
        Captured via {METHOD_LABEL[context.selection_method]}
        {context.selection_truncated ? ' · truncated to 20,000 characters' : ''} · secrets are
        redacted before sending
      </p>
    </div>
  )
}
