/**
 * Smart conversational voice vs. technical detail splitter + TTS speech controller
 * that drives 3D Orb audio-reactive vibration while Clickachu speaks.
 */

export interface VoiceSplitResult {
  /** Short, natural spoken response (no raw file paths, no code blocks, no markdown clutter). */
  voiceSummary: string
  /** Full markdown with <voice>...</voice> wrapper removed for clean rendering in the Side Panel. */
  cleanMarkdown: string
  /** True when the response has file paths, code blocks, tool steps, or lengthy technical content. */
  hasTechnicalDetails: boolean
  /** True while the <voice> tag is still actively streaming and unclosed. */
  isStreamingVoice: boolean
}

const WINDOWS_OR_UNIX_PATH =
  /(?:[A-Za-z]:\\[^\s`"')]+|(?:\/[\w.-]+){2,}|~\/[\w./-]+|\b[\w.-]+\\[\w.\\-]+)/
const CODE_FENCE = /```[\s\S]*?```|`[^`\n]{18,}`/
const TECHNICAL_MARKERS =
  /(?:^|\n)\s*(?:[-*+]\s+.+\n\s*[-*+]\s+.+\n\s*[-*+]\s+|\d+\.\s+.+\n\s*\d+\.\s+|#{1,3}\s+|\|.+\|.+\|)/m

/** Strip <thought>...</thought> and <think>...</think> blocks from text. */
function stripThoughts(raw: string): string {
  let cleaned = raw.replace(/<(?:thought|think)>[\s\S]*?<\/(?:thought|think)>/gi, '')
  const openIdx = cleaned.search(/<(?:thought|think)>/i)
  if (openIdx !== -1) {
    cleaned = cleaned.slice(0, openIdx)
  }
  return cleaned.trim()
}

/** Sanitize a string for natural spoken TTS (remove markdown symbols, raw paths, backticks). */
export function sanitizeForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*([^\\/:*?"<>|\r\n]+)/g, '$1')
    .replace(/(?:\/[\w.-]+)+\/([\w.-]+)/g, '$1')
    .replace(/[#*_~>|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Splits a raw LLM response into:
 * 1) A short conversational `voiceSummary` for the 3D Orb HUD & TTS
 * 2) Full `cleanMarkdown` for the Side Panel
 * 3) `hasTechnicalDetails` flag that triggers automatic Side Panel handoff while continuing to speak
 */
export function splitVoiceAndTechnical(raw: string, hasSteps = false): VoiceSplitResult {
  const withoutThoughts = stripThoughts(raw)
  if (!withoutThoughts) {
    return {
      voiceSummary: '',
      cleanMarkdown: '',
      hasTechnicalDetails: hasSteps,
      isStreamingVoice: false,
    }
  }

  // 1. Check for closed <voice>...</voice> tag
  const closedVoiceMatch = withoutThoughts.match(/<voice>([\s\S]*?)<\/voice>/i)
  if (closedVoiceMatch) {
    const voiceRaw = closedVoiceMatch[1].trim()
    const remainder = withoutThoughts.replace(/<voice>[\s\S]*?<\/voice>/i, '').trim()
    const hasTech =
      hasSteps ||
      remainder.length > 0 ||
      CODE_FENCE.test(withoutThoughts) ||
      WINDOWS_OR_UNIX_PATH.test(withoutThoughts)
    return {
      voiceSummary: sanitizeForSpeech(voiceRaw) || 'Here are the details in the side panel.',
      cleanMarkdown: remainder || voiceRaw,
      hasTechnicalDetails: hasTech,
      isStreamingVoice: false,
    }
  }

  // 2. Check for streaming unclosed <voice>... tag
  const openVoiceMatch = withoutThoughts.match(/<voice>([\s\S]*)$/i)
  if (openVoiceMatch) {
    const streamingVoice = openVoiceMatch[1].trim()
    const beforeVoice = withoutThoughts.slice(0, openVoiceMatch.index).trim()
    return {
      voiceSummary: sanitizeForSpeech(streamingVoice),
      cleanMarkdown: beforeVoice || streamingVoice,
      hasTechnicalDetails: hasSteps,
      isStreamingVoice: true,
    }
  }

  // 3. Fallback heuristic when model doesn't emit <voice> tags
  const hasCode = CODE_FENCE.test(withoutThoughts)
  const hasPath = WINDOWS_OR_UNIX_PATH.test(withoutThoughts)
  const hasStructuredList = TECHNICAL_MARKERS.test(withoutThoughts)
  const isLong = withoutThoughts.length > 240
  const hasTechnicalDetails = hasSteps || hasCode || hasPath || hasStructuredList || isLong

  if (!hasTechnicalDetails) {
    return {
      voiceSummary: sanitizeForSpeech(withoutThoughts),
      cleanMarkdown: withoutThoughts,
      hasTechnicalDetails: false,
      isStreamingVoice: false,
    }
  }

  // Extract first 1-2 conversational sentences before any code block or bullet list
  const beforeCodeOrList = withoutThoughts
    .split(/```|\n\s*[-*+\d]|\n#{1,3}\s/)[0]
    ?.trim()

  const firstSentences =
    beforeCodeOrList
      ?.match(/[^.!?]+[.!?]+/g)
      ?.slice(0, 2)
      .join(' ')
      .trim() || beforeCodeOrList

  let spoken = sanitizeForSpeech(firstSentences || '')
  if (!spoken || spoken.length < 8) {
    spoken = hasSteps
      ? "I've completed that task and opened the full details on the side."
      : "I've pulled up the technical details and code in the side panel for you."
  } else if (spoken.length > 180) {
    spoken = spoken.slice(0, 177).trimEnd() + '…'
  }

  return {
    voiceSummary: spoken,
    cleanMarkdown: withoutThoughts,
    hasTechnicalDetails: true,
    isStreamingVoice: false,
  }
}

/** Strips <voice>...</voice> wrapper tags so Markdown in the Side Panel stays clean. */
export function stripVoiceTags(raw: string): string {
  const closed = raw.replace(/<voice>([\s\S]*?)<\/voice>\s*/gi, (_, inner: string) => {
    // If there is nothing after </voice>, keep the inner text so the message isn't blank
    const rest = raw.replace(/<voice>[\s\S]*?<\/voice>/i, '').trim()
    return rest ? '' : inner.trim()
  })
  return closed.replace(/<\/?voice>/gi, '').trim()
}

/**
 * Manages browser TTS (window.speechSynthesis) and emits a realistic 60Hz audio-envelope
 * level so the 3D Orb vibrates naturally while Clickachu talks.
 */
class SpeechEngine {
  private timer: ReturnType<typeof setInterval> | null = null
  private speaking = false
  private muted = false
  private wordPulse = 0
  private phase = 0
  private levelListeners = new Set<(rms: number) => void>()
  private stateListeners = new Set<(speaking: boolean) => void>()

  constructor() {
    try {
      const savedMute = localStorage.getItem('clickachu:voice_muted')
      if (savedMute === 'true') this.muted = true
    } catch {
      // ignore storage errors
    }
  }

  isMuted(): boolean {
    return this.muted
  }

  setMuted(muted: boolean): void {
    this.muted = muted
    try {
      localStorage.setItem('clickachu:voice_muted', String(muted))
    } catch {
      // ignore
    }
    if (muted) this.stop()
  }

  isSpeaking(): boolean {
    return this.speaking
  }

  onLevel(fn: (rms: number) => void): () => void {
    this.levelListeners.add(fn)
    return () => this.levelListeners.delete(fn)
  }

  onSpeakingChange(fn: (speaking: boolean) => void): () => void {
    this.stateListeners.add(fn)
    return () => this.stateListeners.delete(fn)
  }

  private setSpeakingState(next: boolean): void {
    if (this.speaking === next) return
    this.speaking = next
    for (const fn of this.stateListeners) fn(next)
  }

  private emitLevel(rms: number): void {
    for (const fn of this.levelListeners) fn(rms)
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
    try {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
    } catch {
      // ignore
    }
    this.emitLevel(0)
    this.setSpeakingState(false)
  }

  speak(text: string): void {
    this.stop()
    const clean = sanitizeForSpeech(text)
    if (!clean) return

    // Even if audio is muted or TTS is unavailable, animate the orb's speaking cadence briefly
    // if not muted; if muted, skip audio.
    if (this.muted) return

    const hasSynth = typeof window !== 'undefined' && 'speechSynthesis' in window
    if (!hasSynth) return

    try {
      const utterance = new SpeechSynthesisUtterance(clean)
      utterance.rate = 1.04
      utterance.pitch = 1.02

      const voices = window.speechSynthesis.getVoices()
      const preferred =
        voices.find(
          (v) =>
            v.lang.startsWith('en') &&
            (v.name.includes('Natural') ||
              v.name.includes('Aria') ||
              v.name.includes('Jenny') ||
              v.name.includes('Guy')),
        ) ??
        voices.find((v) => v.lang.startsWith('en') && v.name.includes('Microsoft')) ??
        voices.find((v) => v.lang.startsWith('en'))
      if (preferred) utterance.voice = preferred

      utterance.onboundary = () => {
        this.wordPulse = 0.95
      }

      const startCadence = () => {
        this.setSpeakingState(true)
        this.phase = 0
        this.wordPulse = 0.7
        if (this.timer) clearInterval(this.timer)
        this.timer = setInterval(() => {
          this.phase += 0.32
          this.wordPulse *= 0.86
          // Syllabic modulation combining multi-frequency sine waves + word boundary bursts
          const syllable =
            0.35 +
            0.25 * Math.sin(this.phase) +
            0.2 * Math.sin(this.phase * 2.3) +
            0.15 * Math.cos(this.phase * 0.7)
          const level = Math.min(1, Math.max(0.12, syllable * 0.65 + this.wordPulse * 0.5))
          this.emitLevel(level)
        }, 40)
      }

      utterance.onstart = startCadence
      utterance.onend = () => this.stop()
      utterance.onerror = () => this.stop()

      window.speechSynthesis.speak(utterance)
      // Start cadence immediately in case onstart is delayed by a few ms
      startCadence()
    } catch {
      this.stop()
    }
  }
}

export const speechEngine = new SpeechEngine()
