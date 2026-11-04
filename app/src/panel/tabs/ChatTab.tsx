/**
 * Chat & Technical Details tab (PLAN §9.3):
 * Clean, uncluttered ChatGPT/Claude-inspired layout displaying technical answers, file paths,
 * code blocks, StepTimeline, and ApprovalCards handed off from the 3D Orb HUD or typed directly.
 */
import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApprovalCard } from '@shared/components/ApprovalCard'
import { CopyButton } from '@shared/components/CopyButton'
import { Markdown } from '@shared/components/Markdown'
import { StepTimeline } from '@shared/components/StepTimeline'
import { RunTimer } from '@shared/components/RunTimer'
import { RunDetails } from '@shared/components/RunDetails'
import type { ApprovalDecision } from '@shared/protocol'
import { recordChatRun, startNewChat, useStore } from '@shared/store'
import { stripVoiceTags } from '@shared/voiceSpeech'
import { ws } from '@shared/ws'
import { ghostButtonClass } from '../ui'

function ChatTurn({ runId }: { runId: string }) {
  const run = useStore((s) => s.runs[runId])
  if (!run) return null

  const pendingApprovals = run.steps.filter((st) => st.status === 'awaiting_approval')

  function decide(stepId: string, decision: ApprovalDecision) {
    ws.send({ type: 'approve', run_id: runId, step_id: stepId, decision })
  }

  return (
    <div className="group space-y-2.5 py-2 text-sm">
      {/* User message bubble (sleek right-aligned pill like ChatGPT/Claude) */}
      {run.prompt && (
        <div className="flex items-center justify-end gap-2">
          <RunTimer runId={runId} />
          <div className="max-w-[88%] rounded-2xl rounded-tr-xs border border-white/[0.07] bg-white/[0.07] px-3.5 py-2 text-sm leading-relaxed text-ck-text">
            {run.prompt}
          </div>
        </div>
      )}

      {/* Assistant technical steps, approvals & markdown */}
      <div className="space-y-2.5 pl-0.5">
        <RunDetails run={run} />
        {run.steps.length > 0 && <StepTimeline steps={run.steps} />}

        {pendingApprovals.map((st) => (
          <ApprovalCard
            key={st.step_id}
            step={st}
            onDecide={(decision) => decide(st.step_id, decision)}
          />
        ))}

        {run.text ? (
          <div className="space-y-2 rounded-2xl border border-white/[0.06] bg-white/[0.025] px-3.5 py-2.5">
            <Markdown text={run.text} />
            {run.status === 'done' && (
              <div className="flex items-center justify-between border-t border-white/[0.05] pt-1.5 opacity-80 transition-opacity group-hover:opacity-100">
                <CopyButton getText={() => stripVoiceTags(run.text)} />
                {run.usage && (
                  <span className="font-mono text-[10px] text-ck-muted">
                    {run.usage.input_tokens + run.usage.output_tokens} tok
                  </span>
                )}
              </div>
            )}
          </div>
        ) : (
          run.status === 'running' &&
          pendingApprovals.length === 0 && (
            <div className="flex items-center gap-2 px-1 py-1 text-xs text-ck-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-ck-violet" />
              <span className="ck-thinking">
                {run.steps.length > 0 ? 'Executing steps…' : 'Thinking…'}
              </span>
            </div>
          )
        )}

        {run.status === 'error' && (
          <div className="space-y-1.5 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
            <p className="font-medium text-red-300">{run.error}</p>
            {run.error?.toLowerCase().includes('api key') && (
              <button
                type="button"
                onClick={() => window.clickachu.openSettings()}
                className="mt-1 rounded-lg bg-ck-violet px-3 py-1 text-xs font-medium text-white hover:bg-ck-violet/85"
              >
                Open Settings →
              </button>
            )}
            {run.error?.toLowerCase().includes("can't control admin") && (
              <p className="text-[11px] text-amber-300">
                Tip: Run Clickachu as Administrator to interact with elevated windows.
              </p>
            )}
          </div>
        )}

        {run.steps.some(
          (st) => st.status === 'failed' && st.summary?.toLowerCase().includes('timed out'),
        ) && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-200">
            <span>⚠️</span>
            <span>Approval timed out after 5 minutes — action denied for safety.</span>
          </div>
        )}
        {run.status === 'cancelled' && <p className="px-1 text-xs text-ck-muted">— cancelled</p>}
      </div>
    </div>
  )
}

export function ChatTab() {
  const connection = useStore((s) => s.connection)
  const history = useStore((s) => s.chatHistory)
  const runs = useStore((s) => s.runs)
  const activeRunId = useStore((s) => s.activeRunId)
  const activeConversationId = useStore((s) => s.activeConversationId)
  const activeRun = useStore((s) => (activeRunId ? s.runs[activeRunId] : undefined))
  const [prompt, setPrompt] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const running = activeRunId !== null

  const sessionTokens = history.reduce((sum, runId) => {
    const r = runs[runId]
    return sum + (r?.usage?.input_tokens ?? 0) + (r?.usage?.output_tokens ?? 0)
  }, 0)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [history.length, activeRun?.text, activeRun?.steps.length])

  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [prompt])

  function send(e?: FormEvent) {
    e?.preventDefault()
    const text = prompt.trim()
    if (!text || running) return
    const convId = activeConversationId || `conv_${crypto.randomUUID()}`
    if (!activeConversationId) {
      useStore.setState({ activeConversationId: convId })
    }
    const id = `run_${crypto.randomUUID()}`
    recordChatRun({ runId: id, prompt: text })
    if (ws.send({ type: 'run', run_id: id, text, conversation_id: convId })) {
      setPrompt('')
    } else {
      useStore.setState((s) => ({
        activeRunId: null,
        runs: { ...s.runs, [id]: {
          ...s.runs[id], status: 'error', finishedAt: Date.now(),
          error: 'Not sent: Clickachu core is disconnected.',
        } },
      }))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* Minimal top session bar */}
      {history.length > 0 && (
        <div className="flex items-center justify-between pb-1 text-xs">
          <button
            type="button"
            onClick={() => startNewChat()}
            disabled={running}
            className={`${ghostButtonClass} flex items-center gap-1 py-0.5 px-2.5 text-[11px]`}
            title="Start a fresh conversation"
          >
            <span className="text-ck-violet font-semibold">+</span>
            <span>New chat</span>
          </button>

          {sessionTokens > 0 && (
            <span
              className="font-mono text-[10px] text-ck-muted/75"
              title={`Session context tokens: ${sessionTokens.toLocaleString()}`}
            >
              ~{sessionTokens.toLocaleString()} tok
            </span>
          )}
        </div>
      )}

      {/* Conversation & Technical Details Stream */}
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {history.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.03] text-base">
              ✨
            </div>
            <p className="text-xs font-medium text-ck-text/90">Technical Details &amp; Workspace</p>
            <p className="mt-1 text-[11px] leading-relaxed text-ck-muted">
              Code blocks, file paths, diffs, and tool approvals appear here automatically while
              Clickachu speaks to you.
            </p>
          </div>
        ) : (
          history.map((id) => <ChatTurn key={id} runId={id} />)
        )}
      </div>

      {/* Sleek Unified Bottom Input Pill */}
      <form
        onSubmit={send}
        className="ck-input-pill flex shrink-0 items-end gap-2 rounded-2xl px-3 py-2"
      >
        <textarea
          ref={inputRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
          rows={1}
          placeholder="Ask a follow-up or describe a task…"
          spellCheck={false}
          className="w-full resize-none bg-transparent py-0.5 text-sm leading-5 text-ck-text outline-none placeholder:text-ck-muted/70"
        />

        {running && activeRunId ? (
          <button
            type="button"
            onClick={() => ws.send({ type: 'cancel', run_id: activeRunId })}
            title="Stop"
            className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-red-500/20 text-red-300 ring-1 ring-red-400/40 transition-colors hover:bg-red-500/30"
          >
            <span className="h-2.5 w-2.5 rounded-xs bg-current" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={connection !== 'connected' || running || !prompt.trim()}
            title="Send (Enter)"
            className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ck-violet text-white transition-all hover:bg-ck-violet/90 disabled:bg-white/[0.06] disabled:text-ck-muted/40"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-3.5 w-3.5"
            >
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        )}
      </form>
    </div>
  )
}
