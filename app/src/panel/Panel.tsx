/** Side panel (PLAN §9.3): sleek header + segmented tabs (Chat · Memory · Log · Settings), sliding in from the right. */
import { useEffect, useState } from 'react'
import { ReconnectingBanner } from '@shared/components/ReconnectingBanner'
import { recordChatRun, useStore } from '@shared/store'
import { ws } from '@shared/ws'
import { ChatTab } from './tabs/ChatTab'
import { LogTab } from './tabs/LogTab'
import { MemoryTab } from './tabs/MemoryTab'
import { SettingsTab } from './tabs/SettingsTab'

const AUTO_HIDE_MS = 12_000

const STATUS_DOT = {
  connected: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.7)]',
  connecting: 'bg-amber-400 animate-pulse',
  disconnected: 'bg-amber-400',
  failed: 'bg-red-400',
} as const

const TABS = [
  { id: 'chat', label: 'Details & Chat' },
  { id: 'memory', label: 'Memory' },
  { id: 'log', label: 'Log' },
  { id: 'settings', label: 'Settings' },
] as const

type TabId = (typeof TABS)[number]['id']

function Header({
  pinned,
  onTogglePin,
}: {
  pinned: boolean
  onTogglePin: () => void
}) {
  const connection = useStore((s) => s.connection)
  const detail = useStore((s) => s.connectionDetail)
  const activeRunId = useStore((s) => s.activeRunId)
  const lastRunId = useStore((s) => s.lastRunId)
  const targetId = activeRunId ?? lastRunId
  const runPrompt = useStore((s) => (targetId ? s.runs[targetId]?.prompt : undefined))
  const title = runPrompt?.trim() || (activeRunId ? 'Working…' : 'Clickachu')

  return (
    <header className="flex items-center gap-2.5 px-4 pt-3.5 pb-2">
      <span
        title={detail || connection}
        className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[connection]}`}
      />
      <h1
        title={title}
        className="min-w-0 flex-1 truncate text-sm font-medium tracking-tight text-ck-text"
      >
        {title}
      </h1>
      {activeRunId && (
        <button
          type="button"
          onClick={() => ws.send({ type: 'cancel', run_id: activeRunId })}
          className="shrink-0 rounded-full border border-red-400/30 bg-red-500/15 px-2.5 py-0.5 text-[11px] font-medium text-red-200 transition-colors hover:bg-red-500/25"
        >
          Stop
        </button>
      )}
      <button
        type="button"
        aria-pressed={pinned}
        title={pinned ? 'Pinned (panel stays open)' : 'Pin panel open'}
        onClick={onTogglePin}
        className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
          pinned
            ? 'border-ck-violet/50 bg-ck-violet/20 text-violet-200'
            : 'border-white/[0.08] bg-white/[0.03] text-ck-muted hover:text-ck-text'
        }`}
      >
        {pinned ? 'Pinned' : 'Pin'}
      </button>
      <button
        type="button"
        aria-label="Close panel"
        onClick={() => window.clickachu.hidePanel()}
        className="flex h-6 w-6 items-center justify-center rounded-full text-xs text-ck-muted transition-colors hover:bg-white/10 hover:text-ck-text"
      >
        ✕
      </button>
    </header>
  )
}

export function Panel() {
  const [tab, setTab] = useState<TabId>('chat')
  const [pinned, setPinned] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [autoHideArmed, setAutoHideArmed] = useState(false)
  const activeRunId = useStore((s) => s.activeRunId)
  const connection = useStore((s) => s.connection)
  const connectionDetail = useStore((s) => s.connectionDetail)

  const [shown, setShown] = useState(true)

  useEffect(
    () =>
      window.clickachu.onPanelShown(() => {
        setShown(false)
        requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)))
      }),
    [],
  )

  useEffect(
    () =>
      window.clickachu.onPanelHandoff((handoff) => {
        recordChatRun({
          runId: handoff.runId,
          prompt: handoff.question,
          answer: handoff.answer,
          mode: handoff.mode,
        })
        setTab('chat')
      }),
    [],
  )

  useEffect(
    () =>
      window.clickachu.onOpenSettings(() => {
        setTab('settings')
      }),
    [],
  )

  // Arm auto-hide when an active run finishes (§9.3).
  useEffect(() => {
    const offs = [
      ws.on('run.started', () => setAutoHideArmed(false)),
      ws.on('run.finished', () => setAutoHideArmed(true)),
      ws.on('run.error', () => setAutoHideArmed(true)),
    ]
    return () => offs.forEach((off) => off())
  }, [])

  useEffect(() => {
    if (!autoHideArmed || pinned || hovered || activeRunId !== null || tab !== 'chat') return
    const timer = setTimeout(() => {
      setAutoHideArmed(false)
      window.clickachu.hidePanel()
    }, AUTO_HIDE_MS)
    return () => clearTimeout(timer)
  }, [autoHideArmed, pinned, hovered, activeRunId, tab])

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`flex h-full flex-col border-l border-white/[0.08] bg-[#0c0c16]/96 backdrop-blur-2xl transition-transform duration-200 ease-out ${
        shown ? 'translate-x-0' : 'translate-x-full'
      }`}
    >
      <Header pinned={pinned} onTogglePin={() => setPinned((p) => !p)} />
      <ReconnectingBanner status={connection} detail={connectionDetail} />

      {/* Sleek Segmented Pill Navigation */}
      <div className="px-4 py-1.5">
        <nav
          className="grid grid-cols-4 gap-1 rounded-xl border border-white/[0.06] bg-white/[0.03] p-1"
          role="tablist"
        >
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`truncate rounded-lg px-2 py-1 text-xs font-medium transition-all ${
                tab === t.id
                  ? 'bg-white/[0.11] text-ck-text shadow-xs'
                  : 'text-ck-muted hover:text-ck-text'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      <main className="flex min-h-0 flex-1 flex-col px-4 pt-2 pb-3.5">
        <div className={tab === 'chat' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
          <ChatTab />
        </div>
        {tab === 'memory' && <MemoryTab />}
        {tab === 'log' && <LogTab />}
        {tab === 'settings' && <SettingsTab />}
      </main>
    </div>
  )
}
