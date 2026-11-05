/**
 * ApprovalCard (PLAN §9.3): tool name, monospace preview (line-diff view for write_file),
 * Approve / Always / Deny buttons, and Enter = approve / Esc = deny when focused.
 */
import { type KeyboardEvent, useEffect, useRef } from 'react'
import type { ApprovalDecision } from '../protocol'
import type { StepState } from '../store'

interface ApprovalCardProps {
  step: StepState
  onDecide: (decision: ApprovalDecision) => void
}

function isDiffView(tool: string, preview: string): boolean {
  if (tool === 'write_file') return true
  const lines = preview.split('\n')
  return lines.some((l) => l.startsWith('@@') || l.startsWith('--- ') || l.startsWith('+++ '))
}

function DiffPreview({ text, args }: { text: string; args?: Record<string, unknown> }) {
  // If preview isn't already a line diff and write_file args.content is available, render lines with +.
  const raw =
    text.includes('\n') || text.startsWith('---') || text.startsWith('@@')
      ? text
      : typeof args?.content === 'string'
        ? `+++ ${typeof args.path === 'string' ? args.path : 'file'}\n` +
          args.content
            .split('\n')
            .map((line) => `+${line}`)
            .join('\n')
        : text

  const lines = raw.split('\n')
  return (
    <div className="max-h-48 overflow-auto rounded border border-ck-border bg-black/50 p-2 font-mono text-[11px] leading-relaxed">
      {lines.map((line, idx) => {
        let cls = 'text-ck-text'
        if (line.startsWith('+++') || line.startsWith('---')) {
          cls = 'text-ck-muted font-semibold'
        } else if (line.startsWith('@@')) {
          cls = 'text-ck-violet'
        } else if (line.startsWith('+')) {
          cls = 'bg-emerald-500/15 text-emerald-300'
        } else if (line.startsWith('-')) {
          cls = 'bg-red-500/15 text-red-300'
        }
        return (
          <div key={idx} className={`whitespace-pre px-1 ${cls}`}>
            {line || ' '}
          </div>
        )
      })}
    </div>
  )
}

export function ApprovalCard({ step, onDecide }: ApprovalCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const preview = step.preview ?? step.summary

  useEffect(() => {
    cardRef.current?.focus()
  }, [step.step_id])

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onDecide('deny')
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
      // If a specific button has focus, let its native click handler run.
      if (e.target instanceof HTMLButtonElement) return
      e.preventDefault()
      e.stopPropagation()
      onDecide('approve')
    }
  }

  return (
    <div
      ref={cardRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      role="region"
      aria-label={`Approval required for ${step.tool}`}
      className="space-y-2 rounded-lg border border-amber-400/50 bg-amber-500/10 p-3 text-xs outline-none focus-within:border-amber-300"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
          <span className="font-medium text-amber-200">Approve action</span>
          <span className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[11px] text-ck-violet">
            {step.tool}
          </span>
        </div>
        <span className="text-[10px] text-ck-muted">Enter = Approve · Esc = Deny</span>
      </div>

      {isDiffView(step.tool, preview) ? (
        <DiffPreview text={preview} args={step.args} />
      ) : (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-ck-border bg-black/50 p-2 font-mono text-[11px] text-ck-text">
          {preview}
        </pre>
      )}

      <div className="flex items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => onDecide('approve')}
          className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500"
        >
          Approve
        </button>
        {step.tool === 'desktop_action' && (
          <button type="button" onClick={() => onDecide('task')}
            className="rounded-md border border-ck-violet/60 px-3 py-1 text-xs text-ck-text">
            Allow this window for this task
          </button>
        )}
        {!['execute_code', 'desktop_action', 'workspace_files'].includes(step.tool) && <button
          type="button"
          onClick={() => onDecide('always')}
          className="rounded-md border border-ck-violet/60 bg-ck-violet/20 px-3 py-1 text-xs font-medium text-ck-text hover:bg-ck-violet/30"
        >
          Always
        </button>}
        <button
          type="button"
          onClick={() => onDecide('deny')}
          className="rounded-md border border-red-400/40 bg-red-500/15 px-3 py-1 text-xs font-medium text-red-200 hover:bg-red-500/25"
        >
          Deny
        </button>
      </div>
    </div>
  )
}
