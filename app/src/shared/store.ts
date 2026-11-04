/** Shared renderer state (Zustand), fed by WebSocket events. */
import { create } from 'zustand'
import { api } from './api'
import type { MemoryRef, OrbStateName, RunMode, Tier, Usage } from './protocol'
import type { ConnectionStatus, WsClient } from './ws'

export type RunStatus = 'running' | 'done' | 'cancelled' | 'error'

export interface StepState {
  step_id: string
  tool: string
  summary: string
  tier: Tier
  status: 'running' | 'awaiting_approval' | 'ok' | 'failed'
  verified: boolean | null
  startedAt: number
  durationMs: number | null
  args?: Record<string, unknown>
  argsJson?: string
  resultSummary?: string
  preview?: string
  decision?: string
}

export interface RunState {
  mode: RunMode
  prompt?: string
  text: string
  steps: StepState[]
  status: RunStatus
  error?: string
  usage?: Usage
}

export interface DailyUsage {
  date: string
  inputTokens: number
  outputTokens: number
  totalTokens: number
  costUsd: number
}

function todayDateString(): string {
  return new Date().toISOString().slice(0, 10)
}

const DAILY_USAGE_STORAGE_KEY = 'clickachu:today_usage'

function loadDailyUsage(): DailyUsage {
  const today = todayDateString()
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(DAILY_USAGE_STORAGE_KEY) : null
    if (raw) {
      const parsed = JSON.parse(raw) as DailyUsage
      if (parsed.date === today) {
        return parsed
      }
    }
  } catch {
    // Ignore storage parse errors
  }
  return { date: today, inputTokens: 0, outputTokens: 0, totalTokens: 0, costUsd: 0 }
}

function saveDailyUsage(usage: DailyUsage): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(DAILY_USAGE_STORAGE_KEY, JSON.stringify(usage))
    }
  } catch {
    // Ignore storage write errors
  }
}

interface ClickachuState {
  connection: ConnectionStatus
  connectionDetail?: string
  runs: Record<string, RunState>
  /** Ordered list of run IDs displayed in the panel's Chat tab. */
  chatHistory: string[]
  orbState: OrbStateName
  /** The run currently streaming, if any (drives the panel header's Stop). */
  activeRunId: string | null
  /** Latest run ID shown in the panel header. */
  lastRunId: string | null
  /** Latest `memory.saved`; `seq` changes on every event so repeats re-trigger the toast. */
  lastSaved: { memory: MemoryRef; seq: number } | null
  /** Accumulated usage for today (sum of run.finished events). */
  todayUsage: DailyUsage
  /** Current active conversation ID (groups multiple run turns into one session). */
  activeConversationId: string | null
}

export const useStore = create<ClickachuState>(() => ({
  connection: 'disconnected',
  runs: {},
  chatHistory: [],
  orbState: 'idle',
  activeRunId: null,
  lastRunId: null,
  lastSaved: null,
  todayUsage: loadDailyUsage(),
  activeConversationId: null,
}))

export function startNewChat(): void {
  useStore.setState({
    activeConversationId: `conv_${crypto.randomUUID()}`,
    chatHistory: [],
  })
}

let savedSeq = 0

function endRun(runId: string): Partial<ClickachuState> {
  const { activeRunId } = useStore.getState()
  return activeRunId === runId ? { activeRunId: null } : {}
}

function patchRun(runId: string, patch: (run: RunState) => Partial<RunState>): void {
  useStore.setState((s) => {
    const run = s.runs[runId] ?? { mode: 'ask', text: '', steps: [], status: 'running' }
    return { runs: { ...s.runs, [runId]: { ...run, ...patch(run) } } }
  })
}

function patchStep(runId: string, stepId: string, patch: Partial<StepState>): void {
  patchRun(runId, (run) => ({
    steps: run.steps.map((st) => (st.step_id === stepId ? { ...st, ...patch } : st)),
  }))
}

/** Record a run's prompt/answer in the panel's Chat history (used by ChatTab and QuickBar hand-off). */
export function recordChatRun(opts: {
  runId: string
  prompt: string
  answer?: string
  mode?: RunMode
}): void {
  useStore.setState((s) => {
    const existing = s.runs[opts.runId]
    const nextRun: RunState = existing
      ? {
          ...existing,
          prompt: opts.prompt || existing.prompt,
          text: existing.text || opts.answer || '',
          mode: opts.mode ?? existing.mode,
        }
      : {
          mode: opts.mode ?? 'ask',
          prompt: opts.prompt,
          text: opts.answer ?? '',
          steps: [],
          status: opts.answer !== undefined ? 'done' : 'running',
        }
    const inHistory = s.chatHistory.includes(opts.runId)
    return {
      runs: { ...s.runs, [opts.runId]: nextRun },
      chatHistory: inHistory ? s.chatHistory : [...s.chatHistory, opts.runId],
      lastRunId: opts.runId,
    }
  })
}

function hydrateStepLogs(runId: string): void {
  api
    .getLog(runId)
    .then((entries) => {
      const byStep = new Map(entries.map((e) => [e.step_id, e]))
      patchRun(runId, (run) => ({
        steps: run.steps.map((st) => {
          const entry = byStep.get(st.step_id)
          if (!entry) return st
          return {
            ...st,
            argsJson: entry.args_json || st.argsJson,
            resultSummary: entry.result_summary ?? st.resultSummary ?? st.summary,
            durationMs: entry.duration_ms ?? st.durationMs,
            decision: entry.decision,
          }
        }),
      }))
    })
    .catch(() => {
      // Non-fatal: timeline falls back to WS event fields.
    })
}

/** Wire a client's events into the store. Call once per page; returns an unsubscribe. */
export function bindStore(client: WsClient): () => void {
  const offs = [
    client.onStatus((connection, connectionDetail) =>
      useStore.setState({ connection, connectionDetail }),
    ),
    client.on('run.started', (m) => {
      patchRun(m.run_id, () => ({ mode: m.mode, status: 'running' }))
      useStore.setState((s) => ({
        activeRunId: m.run_id,
        lastRunId: m.run_id,
        // Agent runs always appear in the panel's Chat history.
        chatHistory:
          m.mode === 'agent' && !s.chatHistory.includes(m.run_id)
            ? [...s.chatHistory, m.run_id]
            : s.chatHistory,
      }))
    }),
    client.on('token', (m) => patchRun(m.run_id, (run) => ({ text: run.text + m.delta }))),
    client.on('step.started', (m) =>
      patchRun(m.run_id, (run) => ({
        steps: [
          ...run.steps,
          {
            step_id: m.step_id,
            tool: m.tool,
            summary: m.summary,
            tier: m.tier,
            status: 'running',
            verified: null,
            startedAt: Date.now(),
            durationMs: null,
          },
        ],
      })),
    ),
    client.on('approval.request', (m) =>
      patchStep(m.run_id, m.step_id, {
        status: 'awaiting_approval',
        args: m.args,
        argsJson: JSON.stringify(m.args, null, 2),
        preview: m.preview,
      }),
    ),
    client.on('step.finished', (m) => {
      useStore.setState((s) => {
        const run = s.runs[m.run_id]
        if (!run) return {}
        const now = Date.now()
        return {
          runs: {
            ...s.runs,
            [m.run_id]: {
              ...run,
              steps: run.steps.map((st) =>
                st.step_id === m.step_id
                  ? {
                      ...st,
                      status: m.ok ? 'ok' : 'failed',
                      summary: m.summary,
                      resultSummary: m.summary,
                      verified: m.verified,
                      durationMs: st.durationMs ?? Math.max(0, now - st.startedAt),
                    }
                  : st,
              ),
            },
          },
        }
      })
      hydrateStepLogs(m.run_id)
    }),
    client.on('run.finished', (m) => {
      patchRun(m.run_id, () => ({
        text: m.text,
        usage: m.usage,
        status: m.ok ? 'done' : 'cancelled',
      }))
      if (m.usage) {
        useStore.setState((s) => {
          const today = todayDateString()
          const current =
            s.todayUsage.date === today
              ? s.todayUsage
              : { date: today, inputTokens: 0, outputTokens: 0, totalTokens: 0, costUsd: 0 }
          const inTokens = current.inputTokens + (m.usage.input_tokens || 0)
          const outTokens = current.outputTokens + (m.usage.output_tokens || 0)
          const cost = Number((current.costUsd + (m.usage.cost_usd || 0)).toFixed(6))
          const updated: DailyUsage = {
            date: today,
            inputTokens: inTokens,
            outputTokens: outTokens,
            totalTokens: inTokens + outTokens,
            costUsd: cost,
          }
          saveDailyUsage(updated)
          return { todayUsage: updated }
        })
      }
      useStore.setState(endRun(m.run_id))
      hydrateStepLogs(m.run_id)
    }),
    client.on('run.error', (m) => {
      patchRun(m.run_id, () => ({ status: 'error', error: m.message }))
      useStore.setState(endRun(m.run_id))
    }),
    client.on('memory.saved', (m) =>
      useStore.setState({ lastSaved: { memory: m.memory, seq: ++savedSeq } }),
    ),
    client.on('orb.state', (m) => useStore.setState({ orbState: m.state })),
  ]
  return () => offs.forEach((off) => off())
}
