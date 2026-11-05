import type { RunState } from '../store'

/** Plans describe the workflow. Native reasoning is shown only when the provider sends it. */
export function RunDetails({ run }: { run: RunState }) {
  return (
    <div className="space-y-2 text-xs">
      {(run.plan || run.metrics) && (
        <details className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
          <summary className="cursor-pointer text-ck-muted">Activity details</summary>
          {run.plan && <div className="mt-2">
            <p className="mb-1 font-medium text-violet-200">Execution plan</p>
            <p className="text-ck-muted">{run.plan}</p>
          </div>}
          {run.metrics && <p className="mt-2 text-[10px] text-ck-muted">
            {run.metrics.model_calls} agent model calls · {run.metrics.actions} actions ·{' '}
            {(run.metrics.elapsed_ms / 1000).toFixed(1)}s
            {run.metrics.model_ms != null && <>
              {' · '}model {(run.metrics.model_ms / 1000).toFixed(1)}s
              {' · '}tools {((run.metrics.tool_ms ?? 0) / 1000).toFixed(1)}s
            </>}
          </p>}
        </details>
      )}
      {run.reasoning && (
        <details className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
          <summary className="cursor-pointer text-ck-muted">Provider reasoning</summary>
          <p className="mt-1 text-[10px] text-ck-muted">Provider-reported reasoning or summary; not a complete internal trace or proof of execution.</p>
          <div className="mt-2 whitespace-pre-wrap text-ck-text/80">{run.reasoning}</div>
        </details>
      )}
      {run.activity && run.status === 'running' && (
        <p className="text-ck-muted" role="status">{run.activity}</p>
      )}
    </div>
  )
}
