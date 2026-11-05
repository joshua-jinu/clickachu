/**
 * QuickBar (PLAN §9.2): the one voice + text HUD, opened by Alt+Q (center stage) or an orb click
 * (beside the orb).
 * - Mic is ON by default when summoned from the orb or Ctrl+Alt+V; the transcript streams into the
 *   input live while the user speaks.
 * - Typing stops the mic and calms the orb so the user can focus on writing.
 * - Conversational summaries are spoken aloud and captioned; technical details (paths, code, tool
 *   steps) are handed off to the side panel, after which the bar resets to a fresh message.
 * - Closing the bar never cancels a run: an in-flight run continues in the side panel.
 */
import {
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { api } from '@shared/api'
import { ActionChips } from '@shared/components/ActionChips'
import { ContextChip, SelectionPreview } from '@shared/components/ContextChip'
import { ReconnectingBanner } from '@shared/components/ReconnectingBanner'
import { ResultActions } from '@shared/components/ResultActions'
import { RunTimer } from '@shared/components/RunTimer'
import { RunDetails } from '@shared/components/RunDetails'
import type { PanelHandoff } from '@shared/bridge'
import type { ContextSnapshot, ReplaceMode, RunAction, RunMode } from '@shared/protocol'
import { recordChatRun, type RunState, useStore } from '@shared/store'
import { speechEngine, splitVoiceAndTechnical } from '@shared/voiceSpeech'
import { ws } from '@shared/ws'
import { OrbScene, type OrbVisualState } from '../orb/orbScene'

/** Window padding around the card so its glass shadow isn't clipped. */
const WINDOW_PADDING = 12
const DEFAULT_MAX_HEIGHT = 520
const HERO_ORB_SIZE = 132
const HERO_ORB_RADIUS = 34
/** Displayed orb size once a conversation is on screen, so the answer gets the room. */
const COMPACT_ORB_SIZE = 72

interface Exchange {
  question: string
  runId: string
}

const CHIP_LABEL: Partial<Record<RunAction, string>> = {
  summarise: 'Summarise',
  explain: 'Explain',
  translate: 'Translate',
  rewrite: 'Rewrite',
  fix: 'Fix',
  do: 'Do',
}

const isRunning = (run: RunState | undefined): boolean =>
  run === undefined || run.status === 'running'

function AutoTextarea(props: {
  value: string
  onChange: (v: string) => void
  onFocus?: () => void
  onBlur?: () => void
  onSubmit: () => void
  placeholder: string
  inputRef?: RefObject<HTMLTextAreaElement>
  className?: string
}) {
  const local = useRef<HTMLTextAreaElement>(null)
  const ref = props.inputRef ?? local

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [props.value, ref])

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      props.onSubmit()
    }
  }

  return (
    <textarea
      ref={ref}
      rows={1}
      value={props.value}
      onFocus={props.onFocus}
      onBlur={props.onBlur}
      onChange={(e) => props.onChange(e.target.value)}
      onKeyDown={onKeyDown}
      placeholder={props.placeholder}
      spellCheck={false}
      className={`w-full resize-none bg-transparent text-sm leading-6 text-ck-text outline-none placeholder:text-ck-muted/75 ${props.className ?? ''}`}
    />
  )
}

function SavedToast() {
  const saved = useStore((s) => s.lastSaved)
  const [expiredSeq, setExpiredSeq] = useState<number | null>(null)
  const [undoneSeq, setUndoneSeq] = useState<number | null>(null)
  const seq = saved?.seq

  useEffect(() => {
    if (seq === undefined) return
    const timer = setTimeout(() => setExpiredSeq(seq), 7_000)
    return () => clearTimeout(timer)
  }, [seq])

  if (!saved || expiredSeq === saved.seq) return null
  const undone = undoneSeq === saved.seq

  function undo() {
    if (!saved) return
    const { memory, seq: savedSeq } = saved
    api
      .deleteMemory(memory.id)
      .then(() => setUndoneSeq(savedSeq))
      .catch((err: unknown) => console.warn('[memory] undo failed', err))
  }

  return (
    <div className="flex shrink-0 items-center justify-between gap-2 rounded-xl border border-ck-violet/25 bg-ck-violet/10 px-3 py-1.5 text-xs text-ck-text">
      {undone ? (
        <span className="text-ck-muted">Removed from memory.</span>
      ) : (
        <>
          <span className="min-w-0 flex-1 truncate" title={saved.memory.content}>
            Remembered: {saved.memory.content}
          </span>
          <button
            type="button"
            onClick={undo}
            className="shrink-0 font-medium text-ck-violet hover:underline"
          >
            Undo
          </button>
        </>
      )}
    </div>
  )
}

export function QuickBar() {
  const connection = useStore((s) => s.connection)
  const connectionDetail = useStore((s) => s.connectionDetail)
  const runs = useStore((s) => s.runs)
  const orbState = useStore((s) => s.orbState)

  const [prompt, setPrompt] = useState('')
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [maxHeight, setMaxHeight] = useState(DEFAULT_MAX_HEIGHT)
  const [notice, setNotice] = useState('')
  const [context, setContext] = useState<ContextSnapshot | null>(null)
  const [selectionDropped, setSelectionDropped] = useState(false)
  const [showSelection, setShowSelection] = useState(false)
  const [applying, setApplying] = useState(false)
  const [doMode, setDoMode] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [isTypingFocus, setIsTypingFocus] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [voiceMuted, setVoiceMuted] = useState(() => speechEngine.isMuted())
  /** The run whose summary is spoken aloud; survives the reset that follows a panel handoff. */
  const [voiceRunId, setVoiceRunId] = useState<string | null>(null)

  const autoSendTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const ignoreNextTranscriptRef = useRef(false)
  const listeningRef = useRef(false)
  /** Text already in the input when the mic started; live transcripts are appended to it. */
  const micPrefixRef = useRef('')
  const spokenRunIdRef = useRef<string | null>(null)
  const handedOffSetRef = useRef<Set<string>>(new Set())
  const runActionRef = useRef<(action: RunAction, explicitContextId?: string | null) => void>(
    () => undefined,
  )

  const shellRef = useRef<HTMLDivElement>(null)
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sceneRef = useRef<OrbScene | null>(null)

  const focusPrompt = useCallback(() => promptRef.current?.focus(), [])

  const last = exchanges.at(-1)
  const lastRun = last ? runs[last.runId] : undefined
  const running = last !== undefined && isRunning(lastRun)

  const answerText = lastRun?.text ?? ''
  const hasSteps = (lastRun?.steps.length ?? 0) > 0 || lastRun?.mode === 'agent'
  const split = splitVoiceAndTechnical(answerText, hasSteps)

  const voiceRun = voiceRunId ? runs[voiceRunId] : undefined
  const voiceSplit = splitVoiceAndTechnical(
    voiceRun?.text ?? '',
    (voiceRun?.steps.length ?? 0) > 0 || voiceRun?.mode === 'agent',
  )

  // Determine the hero orb's visual state
  const heroOrbState: OrbVisualState =
    lastRun?.status === 'error' || orbState === 'error'
      ? 'error'
      : isSpeaking
        ? 'speaking'
        : isListening
          ? 'listening'
          : running
            ? lastRun?.steps.some((s) => s.status === 'awaiting_approval')
              ? 'attention'
              : lastRun?.steps.length
                ? 'acting'
                : 'thinking'
            : prompt.trim().length > 0 || isTypingFocus
              ? 'typing'
              : 'idle'

  // Mount the hero orb scene
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const scene = new OrbScene(canvas, HERO_ORB_SIZE, HERO_ORB_RADIUS)
    sceneRef.current = scene
    scene.start()

    const handleContextLost = (e: Event) => e.preventDefault()
    const handleContextRestored = () => {
      sceneRef.current?.dispose()
      const next = new OrbScene(canvas, HERO_ORB_SIZE, HERO_ORB_RADIUS)
      sceneRef.current = next
      next.start()
    }
    canvas.addEventListener('webglcontextlost', handleContextLost, false)
    canvas.addEventListener('webglcontextrestored', handleContextRestored, false)
    return () => {
      canvas.removeEventListener('webglcontextlost', handleContextLost)
      canvas.removeEventListener('webglcontextrestored', handleContextRestored)
      sceneRef.current?.dispose()
      sceneRef.current = null
    }
  }, [])

  useEffect(() => {
    sceneRef.current?.setState(heroOrbState)
  }, [heroOrbState])

  useLayoutEffect(() => {
    listeningRef.current = isListening
  }, [isListening])

  // Microphone RMS levels (from sidecar) and TTS speech cadence levels drive the orb.
  useEffect(() => {
    const offMic = ws.on('voice.level', (msg) => {
      if (listeningRef.current) sceneRef.current?.setLevel(msg.rms)
    })
    const offTtsLevel = speechEngine.onLevel((rms) => {
      sceneRef.current?.setLevel(rms)
    })
    const offTtsState = speechEngine.onSpeakingChange((speaking) => {
      setIsSpeaking(speaking)
    })
    return () => {
      offMic()
      offTtsLevel()
      offTtsState()
    }
  }, [])

  // Local Web Audio analyser for smooth 60fps vibration while listening
  useEffect(() => {
    if (!isListening) return
    let cancelled = false
    let audioCtx: AudioContext | null = null
    let mediaStream: MediaStream | null = null
    let raf = 0

    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        mediaStream = stream
        audioCtx = new AudioContext()
        const source = audioCtx.createMediaStreamSource(stream)
        const analyser = audioCtx.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)
        const data = new Uint8Array(analyser.frequencyBinCount)

        const tick = () => {
          if (cancelled) return
          analyser.getByteTimeDomainData(data)
          let sum = 0
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128
            sum += v * v
          }
          const rms = Math.sqrt(sum / data.length)
          sceneRef.current?.setLevel(Math.min(1, rms * 4.8))
          raf = requestAnimationFrame(tick)
        }
        raf = requestAnimationFrame(tick)
      })
      .catch(() => {
        // Sidecar voice.level WS events still drive the orb if getUserMedia is unavailable
      })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      mediaStream?.getTracks().forEach((t) => t.stop())
      void audioCtx?.close().catch(() => {})
    }
  }, [isListening])

  // Keep main informed so it doesn't hide the HUD on blur while listening, running, or speaking.
  const keepOpen = running || isListening || isSpeaking
  useEffect(() => {
    window.clickachu.setQuickBarRunActive(keepOpen)
  }, [keepOpen])

  // Report the card's natural height whenever it changes.
  useLayoutEffect(() => {
    const el = shellRef.current
    if (!el) return
    const report = () => window.clickachu.reportQuickBarHeight(el.offsetHeight)
    const ro = new ResizeObserver(report)
    ro.observe(el)
    report()
    return () => ro.disconnect()
  }, [])

  const runningRef = useRef(running)
  const exchangesRef = useRef(exchanges)
  const applyRef = useRef<(mode: ReplaceMode) => void>(() => undefined)
  const canReplaceRef = useRef(false)
  useLayoutEffect(() => {
    runningRef.current = running
    exchangesRef.current = exchanges
  }, [running, exchanges])

  /** Live / final transcript appended to whatever was typed before the mic started. */
  const withMicPrefix = useCallback(
    (text: string) => [micPrefixRef.current, text.trim()].filter(Boolean).join(' '),
    [],
  )

  // Start / stop microphone helpers
  const startMic = useCallback(async () => {
    speechEngine.stop()
    ignoreNextTranscriptRef.current = false
    micPrefixRef.current = promptRef.current?.value.trim() ?? ''
    listeningRef.current = true
    setIsListening(true)
    setNotice('')
    try {
      const res = await api.startVoice()
      if (!res.ok) {
        listeningRef.current = false
        setIsListening(false)
        setNotice('Microphone unavailable — type below instead.')
      }
    } catch {
      listeningRef.current = false
      setIsListening(false)
    }
  }, [])

  const stopMic = useCallback(
    async (discardTranscript = false) => {
      if (discardTranscript) ignoreNextTranscriptRef.current = true
      listeningRef.current = false
      setIsListening(false)
      try {
        const res = await api.stopVoice()
        if (!discardTranscript && res.text?.trim()) setPrompt(withMicPrefix(res.text))
      } catch {
        // ignore
      }
    },
    [withMicPrefix],
  )

  const toggleMic = useCallback(() => {
    if (isListening) void stopMic(false)
    else void startMic()
  }, [isListening, startMic, stopMic])

  /** Clear the on-screen conversation so the next message starts fresh. */
  const resetConversation = useCallback(() => {
    setExchanges([])
    setPrompt('')
    setDoMode(false)
    setShowSelection(false)
    useStore.setState({ activeConversationId: null })
  }, [])

  /** Show a run in the side panel, then reset the bar for a new message. */
  const handOff = useCallback(
    (handoff: PanelHandoff) => {
      if (handoff.runId) handedOffSetRef.current.add(handoff.runId)
      window.clickachu.openPanelInChat(handoff)
      resetConversation()
    },
    [resetConversation],
  )

  // Hand off to the side panel when a run is in agent mode or streams technical details.
  useEffect(() => {
    if (!last || !lastRun) return
    const needsAssistant =
      lastRun.mode === 'agent' ||
      lastRun.steps.length > 0 ||
      (Boolean(lastRun.text) && split.hasTechnicalDetails)

    if (needsAssistant && !handedOffSetRef.current.has(last.runId)) {
      handOff({
        runId: last.runId,
        question: last.question,
        answer: lastRun.text || undefined,
        mode: lastRun.mode,
        conversationId: lastRun.conversationId,
        contextId: lastRun.contextId ?? null,
        keepHudOpen: true,
      })
    }
  }, [handOff, last, lastRun, split.hasTechnicalDetails])

  // When the voice run finishes (or closes its <voice> tag), speak the conversational summary.
  useEffect(() => {
    if (!voiceRunId || !voiceRun) return
    const readyToSpeak =
      (voiceRun.status === 'done' ||
        (!voiceSplit.isStreamingVoice && voiceSplit.hasTechnicalDetails)) &&
      Boolean(voiceSplit.voiceSummary)

    if (readyToSpeak && spokenRunIdRef.current !== voiceRunId) {
      spokenRunIdRef.current = voiceRunId
      speechEngine.speak(voiceSplit.voiceSummary)
    }
  }, [
    voiceRunId,
    voiceRun,
    voiceSplit.voiceSummary,
    voiceSplit.isStreamingVoice,
    voiceSplit.hasTechnicalDetails,
  ])

  // Each time the HUD is shown: reset if idle, start mic if requested, and focus the input.
  useEffect(
    () =>
      window.clickachu.onQuickBarShown(
        ({ maxHeight: mh, context: captured, initialAction, isListening: listening }) => {
          setMaxHeight(mh)
          if (!runningRef.current) {
            setContext(captured)
            setSelectionDropped(false)
            setShowSelection(false)
            setExchanges([])
            setPrompt('')
            setNotice('')
            setDoMode(false)
            if (initialAction) {
              listeningRef.current = false
              setIsListening(false)
              requestAnimationFrame(() => {
                runActionRef.current(initialAction, captured?.id ?? null)
              })
            } else {
              // Default mic ON when opened from the orb or voice hotkey
              if (listening ?? true) {
                void startMic()
              } else {
                listeningRef.current = false
                setIsListening(false)
              }
              requestAnimationFrame(focusPrompt)
            }
          } else {
            if (listening) void startMic()
            requestAnimationFrame(focusPrompt)
          }
        },
      ),
    [startMic, focusPrompt],
  )

  useEffect(() => {
    promptRef.current?.focus()
  }, [])

  const contextId = context && !selectionDropped ? context.id : undefined
  const hasSelection = Boolean(context?.selection) && !selectionDropped

  const start = useCallback(
    (
      question: string,
      text: string,
      append: boolean,
      action?: RunAction,
      mode?: RunMode,
      explicitContextId?: string | null,
    ) => {
      speechEngine.stop()
      const runId = `run_${crypto.randomUUID()}`
      const conversationId =
        useStore.getState().activeConversationId || `conv_${crypto.randomUUID()}`
      useStore.setState({ activeConversationId: conversationId })
      const sourceId = explicitContextId !== undefined ? explicitContextId : (contextId ?? null)
      recordChatRun({ runId, prompt: question, mode, conversationId, contextId: sourceId })
      if (
        !ws.send({
          type: 'run',
          run_id: runId,
          conversation_id: conversationId,
          text,
          mode,
          action,
          context_id: sourceId,
        })
      ) {
        useStore.setState((s) => ({
          activeRunId: null,
          runs: {
            ...s.runs,
            [runId]: {
              ...s.runs[runId],
              status: 'error',
              finishedAt: Date.now(),
              error: 'Not sent: Clickachu core is disconnected.',
            },
          },
        }))
        setNotice('Connecting to Clickachu core — try again in a moment.')
        return false
      }
      setNotice('')
      setVoiceRunId(runId)
      setExchanges((xs) => (append ? [...xs, { question, runId }] : [{ question, runId }]))

      // Explicit agent / fix / do tasks go straight to the side panel; the bar resets.
      if (mode === 'agent' || action === 'do' || action === 'fix') {
        handOff({
          runId,
          question,
          mode: 'agent',
          conversationId,
          contextId: sourceId,
          keepHudOpen: true,
        })
      }
      return true
    },
    [contextId, handOff],
  )

  function runAction(action: RunAction, explicitContextId?: string | null) {
    if (isListening) void stopMic(true)
    if (action === 'do') {
      const typed = prompt.trim()
      if (typed && !running) {
        if (start(typed, typed, false, 'do', 'agent', explicitContextId)) setPrompt('')
      } else {
        setDoMode((v) => !v)
        focusPrompt()
      }
      return
    }
    if (running) return
    const typed = prompt.trim()
    const label = CHIP_LABEL[action] ?? action
    const mode: RunMode = action === 'fix' ? 'agent' : 'ask'
    if (start(typed ? `${label}: ${typed}` : label, typed, false, action, mode, explicitContextId)) {
      setPrompt('')
    }
  }

  useLayoutEffect(() => {
    runActionRef.current = runAction
  })

  const handleTranscript = useCallback(
    (text: string) => {
      listeningRef.current = false
      setIsListening(false)
      if (ignoreNextTranscriptRef.current) {
        ignoreNextTranscriptRef.current = false
        return
      }
      const clean = withMicPrefix(text)
      if (!clean) return
      setPrompt(clean)
      if (autoSendTimer.current) clearTimeout(autoSendTimer.current)
      autoSendTimer.current = setTimeout(() => {
        autoSendTimer.current = null
        const append = exchangesRef.current.length > 0 && !runningRef.current
        if (!runningRef.current && start(clean, clean, append)) setPrompt('')
      }, 450)
    },
    [start, withMicPrefix],
  )

  useEffect(() => {
    // Live transcript: stream interim text into the input while the user is still speaking.
    const unsubPartial = ws.on('voice.partial', (msg) => {
      if (!listeningRef.current || ignoreNextTranscriptRef.current) return
      setPrompt(withMicPrefix(msg.text))
    })
    const unsubWs = ws.on('voice.done', (msg) => handleTranscript(msg.text))
    const unsubIpc = window.clickachu.onVoiceTranscript((text) => handleTranscript(text))
    return () => {
      unsubPartial()
      unsubWs()
      unsubIpc()
      if (autoSendTimer.current) clearTimeout(autoSendTimer.current)
    }
  }, [handleTranscript, withMicPrefix])

  // Typing stops the mic (keeping what's in the box) and calms the orb into 'typing'.
  function onPromptChange(value: string) {
    if (autoSendTimer.current) {
      clearTimeout(autoSendTimer.current)
      autoSendTimer.current = null
    }
    if (isListening) void stopMic(true)
    if (isSpeaking && value.length > 0) speechEngine.stop()
    setPrompt(value)
  }

  async function applyResult(mode: ReplaceMode) {
    const text = runs[exchangesRef.current.at(-1)?.runId ?? '']?.text ?? ''
    if (!context || !text || applying) return
    setApplying(true)
    window.clickachu.setQuickBarRunActive(true)
    try {
      const res = await api.replaceContext({
        context_id: context.id,
        text: split.cleanMarkdown || text,
        mode,
      })
      if (res.ok) {
        speechEngine.stop()
        window.clickachu.setQuickBarRunActive(false)
        window.clickachu.hideQuickBar()
        return
      }
      if (res.reason === 'focus') {
        setNotice('Copied — paste with Ctrl+V')
      } else {
        await navigator.clipboard.writeText(split.cleanMarkdown || text)
        setNotice('Window unavailable. Copied — paste with Ctrl+V')
      }
    } catch {
      setNotice('Could not reach target window. Use Copy instead.')
    } finally {
      setApplying(false)
      window.clickachu.setQuickBarRunActive(runningRef.current)
    }
  }
  useLayoutEffect(() => {
    applyRef.current = (mode) => void applyResult(mode)
  })

  function openInAssistant() {
    if (!last) return
    handOff({
      runId: last.runId,
      question: last.question,
      answer: lastRun?.text,
      mode: lastRun?.mode,
      conversationId: lastRun?.conversationId,
      contextId: selectionDropped ? null : (lastRun?.contextId ?? null),
      keepHudOpen: false,
    })
  }

  function openPanelWithSelection() {
    if (last) {
      openInAssistant()
      return
    }
    const conversationId =
      useStore.getState().activeConversationId || `conv_${crypto.randomUUID()}`
    handOff({ conversationId, contextId: contextId ?? null })
  }

  function sendPrompt() {
    const q = prompt.trim()
    if (!q || running) return
    if (autoSendTimer.current) {
      clearTimeout(autoSendTimer.current)
      autoSendTimer.current = null
    }
    if (isListening) void stopMic(true)
    if (doMode) {
      if (start(q, q, false, 'do', 'agent')) {
        setPrompt('')
        setDoMode(false)
      }
      return
    }
    if (start(q, q, exchanges.length > 0)) setPrompt('')
  }

  /** Stop button: the only thing that cancels a run. */
  const cancel = useCallback(() => {
    speechEngine.stop()
    const id = exchangesRef.current.at(-1)?.runId
    if (id && runningRef.current) ws.send({ type: 'cancel', run_id: id })
  }, [])

  /** Close (✕ / Esc): never cancels — an in-flight run carries on in the side panel. */
  const closeHud = useCallback(() => {
    speechEngine.stop()
    if (autoSendTimer.current) {
      clearTimeout(autoSendTimer.current)
      autoSendTimer.current = null
    }
    if (listeningRef.current) void stopMic(true)
    const current = exchangesRef.current.at(-1)
    if (current && runningRef.current && !handedOffSetRef.current.has(current.runId)) {
      const run = useStore.getState().runs[current.runId]
      handOff({
        runId: current.runId,
        question: current.question,
        answer: run?.text || undefined,
        mode: run?.mode,
        conversationId: run?.conversationId,
        contextId: run?.contextId ?? null,
        keepHudOpen: true,
      })
    }
    window.clickachu.setQuickBarRunActive(false)
    window.clickachu.hideQuickBar()
  }, [handOff, stopMic])

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Enter' && e.ctrlKey && !e.isComposing) {
        e.preventDefault()
        if (canReplaceRef.current) applyRef.current('replace')
        return
      }
      if (e.key !== 'Escape') return
      e.preventDefault()
      closeHud()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closeHud])

  const canReplace = hasSelection && !context?.is_terminal
  const canInsert = Boolean(context?.hwnd)
  const replaceReady = canReplace && !running && Boolean(answerText) && !applying
  useLayoutEffect(() => {
    canReplaceRef.current = replaceReady
  }, [replaceReady])

  function toggleVoiceMute() {
    const next = !voiceMuted
    setVoiceMuted(next)
    speechEngine.setMuted(next)
  }

  // Dynamic status label under the orb
  const statusLabel = isListening
    ? 'Listening… speak or type below'
    : isSpeaking
      ? 'Speaking…'
      : running
        ? lastRun?.steps.some((s) => s.status === 'awaiting_approval')
          ? 'Waiting for your approval in Side Panel…'
          : lastRun?.steps.some((s) => s.status === 'running')
            ? `Running ${lastRun.steps.find((s) => s.status === 'running')?.tool ?? 'task'}…`
            : lastRun?.activity || 'Generating response…'
        : prompt.trim().length > 0
          ? 'Press Enter to send'
          : 'Click the orb to speak, or type below'

  const showConversationalCaption =
    Boolean(last) && (Boolean(split.voiceSummary) || lastRun?.status === 'error')
  const compactOrb = exchanges.length > 0
  const orbDisplaySize = compactOrb ? COMPACT_ORB_SIZE : HERO_ORB_SIZE

  return (
    <div ref={shellRef} style={{ padding: WINDOW_PADDING }}>
      <div
        className="ck-glass relative flex flex-col overflow-hidden rounded-3xl"
        style={{ maxHeight: maxHeight - WINDOW_PADDING * 2 }}
      >
        {/* Header: always visible, never scrolls away */}
        <div className="flex w-full shrink-0 items-center justify-between gap-2 px-4 pt-3 pb-1.5">
          <div className="min-w-0 flex-1">
            {context?.app || hasSelection ? (
              <ContextChip
                context={context}
                dropped={selectionDropped}
                expanded={showSelection}
                onToggle={() => setShowSelection((v) => !v)}
                onDrop={() => {
                  setSelectionDropped(true)
                  setShowSelection(false)
                  useStore.setState({ activeContextId: null })
                }}
              />
            ) : (
              <span className="text-[11px] font-medium tracking-wide text-ck-muted/70">
                Clickachu
              </span>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={toggleVoiceMute}
              title={voiceMuted ? 'Voice responses muted (click to unmute)' : 'Voice responses on'}
              className={`flex h-7 w-7 items-center justify-center rounded-full border text-xs transition-colors ${
                voiceMuted
                  ? 'border-white/10 bg-white/5 text-ck-muted hover:text-ck-text'
                  : 'border-ck-violet/35 bg-ck-violet/15 text-ck-text hover:bg-ck-violet/25'
              }`}
            >
              {voiceMuted ? '🔇' : '🔊'}
            </button>
            <button
              type="button"
              onClick={openPanelWithSelection}
              title="Open in Side Panel (Ctrl+Alt+P toggles panel)"
              className="flex h-7 items-center gap-1 rounded-full border border-white/10 bg-white/5 px-2.5 text-[11px] font-medium text-ck-muted transition-colors hover:border-white/20 hover:text-ck-text"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4"
                aria-hidden="true"
              >
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M14 4v16M6 8h5M6 12h5M6 16h5" />
              </svg>
            </button>
            <button
              type="button"
              onClick={closeHud}
              title="Close (Esc)"
              aria-label="Close"
              className="flex h-7 w-7 items-center justify-center rounded-full text-xs text-ck-muted transition-colors hover:bg-white/10 hover:text-ck-text"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body: scrolls when the answer is long, so header and input stay reachable */}
        <div className="flex min-h-0 flex-1 flex-col items-center gap-3 overflow-y-auto px-5 pb-2">
          {connection !== 'connected' && (
            <ReconnectingBanner
              status={connection}
              detail={connectionDetail}
              className="w-full shrink-0 rounded-xl"
            />
          )}

          {showSelection && context && (
            <div className="w-full shrink-0">
              <SelectionPreview context={context} />
            </div>
          )}

          <div className="flex shrink-0 flex-col items-center justify-center">
            <button
              type="button"
              onClick={toggleMic}
              title={isListening ? 'Listening — click to mute mic' : 'Click orb to speak'}
              className="ck-orb-stage group relative flex items-center justify-center rounded-full outline-none transition-all duration-300 hover:scale-[1.03] active:scale-[0.98]"
              style={{ width: orbDisplaySize + 24, height: orbDisplaySize + 24 }}
            >
              <span
                className={`pointer-events-none absolute inset-1.5 rounded-full border transition-all duration-500 ${
                  isListening
                    ? 'animate-pulse border-cyan-400/40 shadow-[0_0_32px_rgba(63,224,255,0.28)]'
                    : isSpeaking
                      ? 'border-fuchsia-400/40 shadow-[0_0_32px_rgba(224,64,251,0.28)]'
                      : running
                        ? 'border-ck-violet/35 shadow-[0_0_24px_rgba(139,92,246,0.22)]'
                        : 'border-white/[0.06] shadow-[0_0_20px_rgba(79,124,255,0.12)]'
                }`}
              />
              <canvas
                ref={canvasRef}
                width={HERO_ORB_SIZE}
                height={HERO_ORB_SIZE}
                className="pointer-events-none relative z-10 transition-all duration-300"
                style={{ width: orbDisplaySize, height: orbDisplaySize }}
              />
            </button>

            <div className="mt-1 flex items-center gap-1.5 text-xs text-ck-muted" aria-live="polite">
              <span
                className={`h-1.5 w-1.5 rounded-full transition-colors ${
                  isListening
                    ? 'animate-ping bg-cyan-400'
                    : isSpeaking
                      ? 'bg-fuchsia-400'
                      : running
                        ? 'bg-ck-violet'
                        : 'bg-white/30'
                }`}
              />
              <span className="font-medium tracking-tight">{statusLabel}</span>
              {last && <RunTimer runId={last.runId} />}
            </div>
          </div>

          {lastRun && (
            <div className="w-full shrink-0">
              <RunDetails run={lastRun} />
            </div>
          )}
          {showConversationalCaption && (
            <div className="w-full shrink-0 space-y-2 rounded-2xl border border-white/[0.08] bg-white/[0.035] px-4 py-3 text-center">
              {last && (
                <p className="truncate text-[11px] font-medium text-ck-muted/80">{last.question}</p>
              )}
              {lastRun?.status === 'error' ? (
                <div className="space-y-1.5 text-xs text-red-300">
                  <p>{lastRun.error}</p>
                  {lastRun.error?.toLowerCase().includes('api key') && (
                    <button
                      type="button"
                      onClick={() => window.clickachu.openSettings()}
                      className="rounded-full bg-ck-violet px-3 py-1 text-xs font-medium text-white hover:bg-ck-violet/85"
                    >
                      Open Settings →
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-sm leading-relaxed text-ck-text">{split.voiceSummary}</p>
              )}

              {!running && answerText && !split.hasTechnicalDetails && (
                <div className="flex justify-center pt-1">
                  <ResultActions
                    text={split.cleanMarkdown || answerText}
                    canReplace={canReplace}
                    canInsert={canInsert}
                    busy={applying}
                    onReplace={() => void applyResult('replace')}
                    onInsert={() => void applyResult('insert')}
                    onOpenInAssistant={openInAssistant}
                  />
                </div>
              )}
            </div>
          )}

          {/* Contextual action chips (only when text is selected) */}
          {hasSelection && (
            <div className="w-full shrink-0">
              <ActionChips hasSelection={hasSelection} onAction={runAction} disabled={running} />
            </div>
          )}

          {notice && <p className="shrink-0 text-xs text-amber-300">{notice}</p>}
          <div className="w-full shrink-0 empty:hidden">
            <SavedToast />
          </div>
        </div>

        {/* Input pill: pinned to the bottom */}
        <div className="shrink-0 px-5 pt-1 pb-4">
          <div className="ck-input-pill flex w-full items-end gap-2 rounded-2xl px-3 py-2">
            <button
              type="button"
              onClick={toggleMic}
              title={isListening ? 'Microphone is ON (click to mute)' : 'Turn microphone ON'}
              className={`mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-all ${
                isListening
                  ? 'bg-cyan-500/20 text-cyan-300 ring-1 ring-cyan-400/50 shadow-[0_0_12px_rgba(63,224,255,0.3)]'
                  : 'bg-white/[0.06] text-ck-muted hover:bg-white/10 hover:text-ck-text'
              }`}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4"
              >
                {isListening ? (
                  <>
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" x2="12" y1="19" y2="22" />
                  </>
                ) : (
                  <>
                    <line x1="2" x2="22" y1="2" y2="22" />
                    <path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2" />
                    <path d="M5 10v2a7 7 0 0 0 12 5" />
                    <path d="M15 9.34V5a3 3 0 0 0-5.68-1.33" />
                    <path d="M9 9v3a3 3 0 0 0 5.12 2.12" />
                    <line x1="12" x2="12" y1="19" y2="22" />
                  </>
                )}
              </svg>
            </button>

            {doMode && (
              <span className="mb-1 shrink-0 rounded-full bg-ck-violet/25 px-2 py-0.5 text-[11px] font-medium text-violet-200">
                Task
              </span>
            )}

            <div className="flex min-w-0 flex-1 items-center py-0.5">
              <AutoTextarea
                inputRef={promptRef}
                value={prompt}
                onFocus={() => setIsTypingFocus(true)}
                onBlur={() => setIsTypingFocus(false)}
                onChange={onPromptChange}
                onSubmit={sendPrompt}
                placeholder={
                  isListening
                    ? 'Listening… your words appear here'
                    : doMode
                      ? 'Describe a task to carry out…'
                      : exchanges.length > 0
                        ? 'Ask a follow-up, or click mic to speak…'
                        : 'Message Clickachu…'
                }
              />
            </div>

            {running ? (
              <button
                type="button"
                onClick={cancel}
                title="Stop current run"
                className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-500/20 text-red-300 ring-1 ring-red-400/40 transition-colors hover:bg-red-500/30"
              >
                <span className="h-2.5 w-2.5 rounded-xs bg-current" />
              </button>
            ) : (
              <button
                type="button"
                onClick={sendPrompt}
                disabled={!prompt.trim()}
                title="Send (Enter)"
                className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ck-violet text-white transition-all hover:bg-ck-violet/90 disabled:bg-white/[0.06] disabled:text-ck-muted/40"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-4 w-4"
                >
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
