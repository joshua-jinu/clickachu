/**
 * StepTimeline (PLAN §9.3): status icon (✓ / ✗ / spinner / ⏸), tool summary, duration,
 * verified tick, and expandable args / result details.
 */
import { useState } from 'react'
import type { StepState } from '../store'

function StatusIcon({ status }: { status: StepState['status'] }) {
  switch (status) {
    case 'ok':
      return (
        <span className="font-semibold text-emerald-400" title="Succeeded">
          ✓
        </span>
      )
    case 'failed':
      return (
        <span className="font-semibold text-red-400" title="Failed">
          ✗
        </span>
      )
    case 'awaiting_approval':
      return (
        <span className="text-amber-300" title="Awaiting approval">
          ⏸
        </span>
      )
    case 'running':
      return (
        <span
          className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ck-violet border-t-transparent"
          title="Running"
        />
      )
  }
}

function formatDuration(ms: number | null): string {
  if (ms === null) return ''
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function formatJson(raw: string | undefined): string {
  if (!raw) return '{}'
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function StepRow({ step }: { step: StepState }) {
  const [expanded, setExpanded] = useState(false)
  const duration = formatDuration(step.durationMs)

  return (
    <li className="rounded-md border border-ck-border bg-black/25 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-white/5"
      >
        <span className="flex w-4 shrink-0 items-center justify-center">
          <StatusIcon status={step.status} />
        </span>
        <span className="shrink-0 font-mono text-[11px] text-ck-violet">{step.tool}</span>
        <span className="min-w-0 flex-1 truncate text-ck-text" title={step.summary}>
          {step.summary}
        </span>
        {step.verified === true && (
          <span
            className="shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300"
            title="Outcome verified"
          >
            ✓ verified
          </span>
        )}
        {step.verified === false && (
          <span
            className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
            title="Verification did not pass"
          >
            unverified
          </span>
        )}
        {step.summary?.toLowerCase().includes('timed out') && (
          <span
            className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
            title="Approval timed out (denied)"
          >
            timed out
          </span>
        )}
        {step.summary?.toLowerCase().includes("can't control admin windows") && (
          <span
            className="shrink-0 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-300"
            title="Admin window blocked by UIPI"
          >
            admin window
          </span>
        )}
        {duration && <span className="shrink-0 tabular-nums text-ck-muted">{duration}</span>}
        <span className="shrink-0 text-[10px] text-ck-muted">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div className="space-y-1.5 border-t border-ck-border px-2.5 py-2 font-mono text-[11px]">
          <div>
            <span className="text-ck-muted">Args: </span>
            <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap rounded bg-black/40 p-1.5 text-ck-text">
              {formatJson(step.argsJson)}
            </pre>
          </div>
          {(step.resultSummary ?? step.summary) && (
            <div>
              <span className="text-ck-muted">Result: </span>
              <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap rounded bg-black/40 p-1.5 text-ck-text">
                {step.resultSummary ?? step.summary}
              </pre>
            </div>
          )}
          {(step.resultSummary ?? step.summary)
            ?.toLowerCase()
            .includes("can't control admin windows") && (
            <p className="text-[11px] text-amber-300">
              Note: Windows User Interface Privilege Isolation (UIPI) prevents controlling elevated
              windows. Run Clickachu as Administrator if you need to control admin windows.
            </p>
          )}
          {(step.resultSummary ?? step.summary)?.toLowerCase().includes('approval timed out') && (
            <p className="text-[11px] text-amber-300">
              Note: No response was received within the 5-minute timeout window. Action was denied
              safely.
            </p>
          )}
        </div>
      )}
    </li>
  )
}

export function StepTimeline({ steps }: { steps: StepState[] }) {
  if (steps.length === 0) return null
  return (
    <ul className="space-y-1" aria-label="Step timeline">
      {steps.map((st) => (
        <StepRow key={st.step_id} step={st} />
      ))}
    </ul>
  )
}
