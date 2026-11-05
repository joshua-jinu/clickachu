/** Full assistant workspace: conversation, memory, activity, and system configuration. */
import { useEffect, useState } from 'react'
import { ReconnectingBanner } from '@shared/components/ReconnectingBanner'
import { applyPanelHandoff, useStore } from '@shared/store'
import { ws } from '@shared/ws'
import { ChatTab } from './tabs/ChatTab'
import { LogTab } from './tabs/LogTab'
import { MemoryTab } from './tabs/MemoryTab'
import { SettingsTab } from './tabs/SettingsTab'

const TABS = [
  { id: 'chat', label: 'Assistant', marker: '01' },
  { id: 'memory', label: 'Memory', marker: '02' },
  { id: 'log', label: 'Activity', marker: '03' },
  { id: 'settings', label: 'Systems', marker: '04' },
] as const

type TabId = (typeof TABS)[number]['id']

function Header({ tab }: { tab: TabId }) {
  const connection = useStore((s) => s.connection)
  const detail = useStore((s) => s.connectionDetail)
  const activeRunId = useStore((s) => s.activeRunId)
  const lastRunId = useStore((s) => s.lastRunId)
  const targetId = activeRunId ?? lastRunId
  const runPrompt = useStore((s) => (targetId ? s.runs[targetId]?.prompt : undefined))
  const title =
    runPrompt?.trim() ||
    (activeRunId ? 'Working…' : (TABS.find((t) => t.id === tab)?.label ?? 'Assistant'))

  return (
    <header className="ck-assistant-header">
      <div className="ck-header-context">
        <span className="ck-eyebrow">WORKSPACE / {tab.toUpperCase()}</span>
        <h1 title={title}>{title}</h1>
      </div>
      <div className="ck-header-status" title={detail || connection}>
        <span className={`ck-status-led is-${connection}`} />
        <span>{connection === 'connected' ? 'CORE LINKED' : connection.toUpperCase()}</span>
      </div>
      {activeRunId && (
        <button
          type="button"
          onClick={() => ws.send({ type: 'cancel', run_id: activeRunId })}
          className="ck-stop-button"
        >
          STOP RUN
        </button>
      )}
      <button
        type="button"
        aria-label="Hide assistant window"
        onClick={() => window.clickachu.hidePanel()}
        className="ck-hide-window"
      >
        HIDE
      </button>
    </header>
  )
}

export function Panel() {
  const [tab, setTab] = useState<TabId>('chat')
  const [chatFocusRequest, setChatFocusRequest] = useState(0)
  const connection = useStore((s) => s.connection)
  const connectionDetail = useStore((s) => s.connectionDetail)

  useEffect(
    () =>
      window.clickachu.onPanelHandoff((handoff) => {
        applyPanelHandoff(handoff)
        setTab('chat')
      }),
    [],
  )

  useEffect(
    () =>
      window.clickachu.onPanelShown((focusComposer) => {
        if (!focusComposer) return
        setTab('chat')
        setChatFocusRequest((request) => request + 1)
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

  return (
    <div className="ck-app-shell">
      <aside className="ck-sidebar">
        <div className="ck-brand-lockup">
          <div className="ck-brand-mark">
            <span />
          </div>
          <div>
            <strong>CLICKACHU</strong>
            <small>PERSONAL INTELLIGENCE</small>
          </div>
        </div>
        <span className="ck-nav-caption">COMMAND</span>
        <nav className="ck-nav-list" aria-label="Assistant sections" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-label={t.label}
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className="ck-nav-item"
            >
              <span className="ck-nav-marker">{t.marker}</span>
              <span>{t.label}</span>
              {tab === t.id && <i />}
            </button>
          ))}
        </nav>
        <div className="ck-sidebar-foot">
          <div className="ck-mini-orb">
            <span />
          </div>
          <div>
            <small>LOCAL CORE</small>
            <strong>
              {connection === 'connected'
                ? 'OPERATIONAL'
                : connection === 'failed'
                  ? 'CORE OFFLINE'
                  : 'LINKING…'}
            </strong>
          </div>
        </div>
      </aside>
      <section className="ck-main-stage">
        <Header tab={tab} />
        <ReconnectingBanner status={connection} detail={connectionDetail} />
        <main className="ck-content-stage">
          <div className={tab === 'chat' ? 'ck-chat-stage' : 'hidden'}>
            <ChatTab focusRequest={chatFocusRequest} />
          </div>
          {tab === 'memory' && <MemoryTab />}
          {tab === 'log' && <LogTab />}
          {tab === 'settings' && <SettingsTab />}
        </main>
        <footer className="ck-status-footer">
          <span>CLICKACHU DESKTOP SYSTEM</span>
          <span>LOCAL-FIRST · HUMAN-APPROVED ACTIONS</span>
        </footer>
      </section>
    </div>
  )
}
