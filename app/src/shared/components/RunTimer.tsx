import { useEffect, useState } from 'react'
import { useStore } from '../store'

/** Wall time from submission through completion, including tools and approval waits. */
export function RunTimer({ runId }: { runId: string }) {
  const run = useStore((s) => s.runs[runId])
  const [now, setNow] = useState(Date.now)
  const running = run?.status === 'running'

  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(timer)
  }, [running])

  if (run?.startedAt === undefined) return null
  const elapsed = Math.max(0, (run.finishedAt ?? now) - run.startedAt) / 1000
  const label = elapsed < 60
    ? `${elapsed.toFixed(1)} s`
    : `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`

  return (
    <span
      className="shrink-0 font-mono text-[10px] text-ck-muted"
      title="Total elapsed time, including model responses, tools, and approval waits"
      role="timer"
      aria-label={`${running ? 'Elapsed' : 'Total'} time: ${label}`}
    >
      {running ? '⏱ ' : ''}{label}
    </span>
  )
}
