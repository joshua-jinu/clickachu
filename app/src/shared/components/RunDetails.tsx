import type { RunState } from '../store'

/** Plans describe the workflow. Native reasoning is shown only when the provider sends it. */
export function RunDetails({ run }: { run: RunState }) {
  return (
    <div className="space-y-2 text-xs">
      {run.plan && (
        <div className="rounded-lg border border-ck-violet/25 bg-ck-violet/10 p-2.5">
          <p className="mb-1 font-medium text-violet-200">Execution plan</p>
          <p className="text-ck-muted">{run.plan}</p>
        </div>
      )}
      {run.reasoning && (
        <details className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
          <summary className="cursor-pointer text-ck-muted">Model reasoning</summary>
          <p className="mt-1 text-[10px] text-ck-muted">Generated reasoning, not evidence of execution.</p>
          <div className="mt-2 whitespace-pre-wrap text-ck-text/80">{run.reasoning}</div>
        </details>
      )}
    </div>
  )
}
