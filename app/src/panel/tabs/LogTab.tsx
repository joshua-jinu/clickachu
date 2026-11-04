/** Log tab: the action log (PLAN §7 GET /log, §9.3), newest first, with tool/decision/ok filters. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@shared/api'
import type { ActionLogEntry } from '@shared/protocol'
import { useStore } from '@shared/store'
import { errorText, ghostButtonClass, inputClass } from '../ui'

const DECISIONS = ['all', 'auto', 'approved', 'always', 'denied', 'blocked'] as const
type DecisionFilter = (typeof DECISIONS)[number]
type OkFilter = 'all' | 'ok' | 'failed'

function Verdict({ entry }: { entry: ActionLogEntry }) {
  if (entry.ok === null) return <span className="text-ck-muted">…</span>
  const verified = entry.verified ? ' ✓ verified' : ''
  return entry.ok ? (
    <span className="text-emerald-300">ok{verified}</span>
  ) : (
    <span className="text-red-300">failed</span>
  )
}

export function LogTab() {
  const connection = useStore((s) => s.connection)
  const activeRunId = useStore((s) => s.activeRunId)
  const [entries, setEntries] = useState<ActionLogEntry[] | null>(null)
  const [error, setError] = useState('')
  const [toolFilter, setToolFilter] = useState('all')
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>('all')
  const [okFilter, setOkFilter] = useState<OkFilter>('all')

  const load = useCallback(() => {
    api
      .getLog()
      .then((rows) => {
        setEntries(rows)
        setError('')
      })
      .catch((err: unknown) => setError(errorText(err)))
  }, [])

  useEffect(() => {
    if (connection === 'connected') load()
  }, [connection, activeRunId, load])

  const tools = useMemo(() => {
    if (!entries) return []
    return Array.from(new Set(entries.map((e) => e.tool))).sort()
  }, [entries])

  const filtered = useMemo(() => {
    if (!entries) return []
    return entries.filter((e) => {
      if (toolFilter !== 'all' && e.tool !== toolFilter) return false
      if (decisionFilter !== 'all' && e.decision !== decisionFilter) return false
      if (okFilter === 'ok' && e.ok !== true) return false
      if (okFilter === 'failed' && e.ok !== false) return false
      return true
    })
  }, [entries, toolFilter, decisionFilter, okFilter])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Action log</h2>
        <button type="button" onClick={load} className={ghostButtonClass}>
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-3 gap-1.5 text-xs">
        <select
          aria-label="Filter by tool"
          value={toolFilter}
          onChange={(e) => setToolFilter(e.target.value)}
          className={`${inputClass} py-1 text-xs`}
        >
          <option value="all">All tools</option>
          {tools.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <select
          aria-label="Filter by decision"
          value={decisionFilter}
          onChange={(e) => setDecisionFilter(e.target.value as DecisionFilter)}
          className={`${inputClass} py-1 text-xs`}
        >
          {DECISIONS.map((d) => (
            <option key={d} value={d}>
              {d === 'all' ? 'All decisions' : d}
            </option>
          ))}
        </select>

        <select
          aria-label="Filter by outcome"
          value={okFilter}
          onChange={(e) => setOkFilter(e.target.value as OkFilter)}
          className={`${inputClass} py-1 text-xs`}
        >
          <option value="all">All outcomes</option>
          <option value="ok">ok</option>
          <option value="failed">failed</option>
        </select>
      </div>

      {error && <p className="text-xs text-red-300">{error}</p>}
      {entries && filtered.length === 0 && (
        <p className="text-xs text-ck-muted">
          {entries.length === 0 ? 'No actions logged yet.' : 'No entries match the active filters.'}
        </p>
      )}
      <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
        {filtered.map((e) => (
          <li key={e.id} className="rounded-md border border-ck-border bg-black/20 p-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono font-medium text-ck-violet">{e.tool}</span>
              <Verdict entry={e} />
            </div>
            <div className="text-ck-muted">
              {new Date(e.started_at).toLocaleString()} · {e.tier} · {e.decision}
              {e.duration_ms !== null && ` · ${e.duration_ms} ms`}
            </div>
            {e.result_summary && <div className="mt-1 text-ck-text">{e.result_summary}</div>}
          </li>
        ))}
      </ul>
    </div>
  )
}
