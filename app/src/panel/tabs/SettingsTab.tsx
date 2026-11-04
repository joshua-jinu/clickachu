/** Settings tab: model config + API key, browser settings, voice, projects and allowed folders. */
import { type FormEvent, useEffect, useState } from 'react'
import { api } from '@shared/api'
import type { AudioDevice, OllamaStatusResponse, Settings } from '@shared/protocol'
import { useStore } from '@shared/store'
import { buttonClass, errorText, inputClass } from '../ui'
import { ProjectsSection } from './ProjectsSection'
import { RootsSection } from './RootsSection'

const GEMINI_DEFAULTS = {
  router_model: 'gemini-2.5-flash-lite',
  ask_model: 'gemini-2.5-flash',
  agent_model: 'gemini-2.5-flash',
  base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
} as const

const OLLAMA_DEFAULTS = {
  ollama_model: 'llama3.2:3b',
  ollama_base_url: 'http://127.0.0.1:11434/v1',
} as const

const GEMINI_FIELDS: {
  key: 'router_model' | 'ask_model' | 'agent_model' | 'base_url'
  label: string
  placeholder?: string
}[] = [
  { key: 'router_model', label: 'Router model', placeholder: GEMINI_DEFAULTS.router_model },
  { key: 'ask_model', label: 'Ask model', placeholder: GEMINI_DEFAULTS.ask_model },
  { key: 'agent_model', label: 'Agent model', placeholder: GEMINI_DEFAULTS.agent_model },
  { key: 'base_url', label: 'Base URL', placeholder: GEMINI_DEFAULTS.base_url },
]

const RECOMMENDED_MODELS = [
  {
    id: 'qwen2.5:3b',
    label: 'Qwen 2.5 3B',
    badge: 'Recommended',
    desc: 'Best tool calling, reasoning & RAG for laptops (~1.9 GB)',
  },
  {
    id: 'llama3.2:3b',
    label: 'Llama 3.2 3B',
    badge: 'Fast',
    desc: 'Fast, lightweight, native tool calling (~2.0 GB)',
  },
  {
    id: 'qwen2.5:1.5b',
    label: 'Qwen 2.5 1.5B',
    badge: 'Ultra-light',
    desc: 'Lowest latency & memory for low-tier laptops (~1.0 GB)',
  },
]

function SettingsForm() {
  const connection = useStore((s) => s.connection)
  const [form, setForm] = useState<Settings | null>(null)
  const [hasKey, setHasKey] = useState(false)
  const [key, setKey] = useState('')
  const [note, setNote] = useState('')
  const [browserNote, setBrowserNote] = useState('')
  const [sensitiveText, setSensitiveText] = useState('')
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [voiceStatus, setVoiceStatus] = useState<string>('unloaded')
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatusResponse>({ running: false, models: [] })
  const [checkingOllama, setCheckingOllama] = useState(false)

  const refreshOllama = async () => {
    setCheckingOllama(true)
    try {
      const res = await api.getOllamaStatus()
      if (res.running) {
        setOllamaStatus(res)
        return
      }
    } catch {
      // Fall through to direct fetch fallback
    }

    try {
      const raw = await fetch('http://127.0.0.1:11434/api/tags')
      if (raw.ok) {
        const data = (await raw.json()) as { models?: { name: string; size?: number }[] }
        const models = (data.models || []).map((m) => ({
          name: m.name,
          size: m.size || 0,
        }))
        setOllamaStatus({ running: true, models })
        return
      }
    } catch {
      // Direct fetch also failed
    }

    setOllamaStatus({ running: false, models: [], error: 'Ollama not detected at 127.0.0.1:11434' })
    setCheckingOllama(false)
  }

  useEffect(() => {
    if (connection !== 'connected') return
    api
      .getSettings()
      .then(({ has_openrouter_key, voice_model_status, ...settings }) => {
        setForm(settings)
        setHasKey(has_openrouter_key)
        setVoiceStatus(voice_model_status || 'unloaded')
        setSensitiveText(settings.sensitive_domains.join('\n'))
      })
      .catch((err: unknown) => setNote(errorText(err)))

    api
      .getOllamaStatus()
      .then((res) => {
        if (res.running) {
          setOllamaStatus(res)
        } else {
          fetch('http://127.0.0.1:11434/api/tags')
            .then((r) => r.json())
            .then((d: { models?: { name: string; size?: number }[] }) => {
              setOllamaStatus({
                running: true,
                models: (d.models || []).map((m) => ({ name: m.name, size: m.size || 0 })),
              })
            })
            .catch(() => setOllamaStatus(res))
        }
      })
      .catch(() => {
        fetch('http://127.0.0.1:11434/api/tags')
          .then((r) => r.json())
          .then((d: { models?: { name: string; size?: number }[] }) => {
            setOllamaStatus({
              running: true,
              models: (d.models || []).map((m) => ({ name: m.name, size: m.size || 0 })),
            })
          })
          .catch(() => {})
      })

    api
      .getVoiceDevices()
      .then(setDevices)
      .catch((err: unknown) => console.debug('failed to fetch audio devices:', err))
  }, [connection])

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!form) return
    try {
      if (key.trim()) {
        await api.putOpenRouterKey(key.trim())
        setKey('')
      }
      const domains = sensitiveText
        .split('\n')
        .map((d) => d.trim())
        .filter(Boolean)
      const view = await api.putSettings({
        provider: form.provider,
        router_model: form.router_model,
        ask_model: form.ask_model,
        agent_model: form.agent_model,
        base_url: form.base_url,
        ollama_base_url: form.ollama_base_url,
        ollama_model: form.ollama_model,
        translate_language: form.translate_language,
        debug_log_prompts: form.debug_log_prompts,
        browser_channel: form.browser_channel,
        browser_search_url: form.browser_search_url,
        proactive_suggestions: form.proactive_suggestions,
        voice_enabled: form.voice_enabled,
        voice_device: form.voice_device,
        sensitive_domains: domains,
      })
      setHasKey(view.has_openrouter_key)
      setVoiceStatus(view.voice_model_status || 'unloaded')
      setNote('Saved.')
    } catch (err) {
      setNote(errorText(err))
    }
  }

  async function handleOpenBrowser() {
    setBrowserNote('Opening browser…')
    try {
      await api.openBrowser()
      setBrowserNote('Browser opened.')
    } catch (err) {
      setBrowserNote(errorText(err))
    }
  }

  if (!form) return <p className="text-xs text-ck-muted">Settings load once connected…</p>

  const isOllama = form.provider === 'ollama'

  return (
    <form onSubmit={save} className="space-y-4">
      {/* Inference Mode Toggle: Gemini vs Ollama */}
      <div className="space-y-2">
        <label className="block text-xs font-semibold text-ck-text">Model Provider</label>
        <div className="grid grid-cols-2 gap-1.5 rounded-lg border border-ck-border bg-black/40 p-1">
          <button
            type="button"
            onClick={() => setForm({ ...form, provider: 'gemini' })}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-all ${
              !isOllama
                ? 'bg-ck-blue text-white shadow-sm'
                : 'text-ck-muted hover:bg-white/5 hover:text-ck-text'
            }`}
          >
            <span>☁ Google Gemini (Cloud)</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setForm({ ...form, provider: 'ollama' })
              refreshOllama().catch(() => {})
            }}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-all ${
              isOllama
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-ck-muted hover:bg-white/5 hover:text-ck-text'
            }`}
          >
            <span>💻 Ollama (On-Device)</span>
          </button>
        </div>
      </div>

      {/* Gemini Configuration */}
      {!isOllama && (
        <div className="space-y-2 rounded-lg border border-ck-border bg-black/20 p-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-ck-muted">
              Google Gemini Free Tier (Google AI Studio)
            </span>
            <button
              type="button"
              onClick={() => setForm({ ...form, ...GEMINI_DEFAULTS })}
              className="rounded bg-white/10 px-2 py-0.5 text-[11px] text-ck-text hover:bg-white/15"
            >
              Reset to Gemini defaults
            </button>
          </div>
          <label className="block text-xs text-ck-muted">
            Gemini API key{' '}
            {hasKey
              ? '(stored — leave blank to keep)'
              : '(not set — get free key at aistudio.google.com/apikey)'}
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              className={inputClass}
              placeholder={hasKey ? '••••••••' : 'AIzaSy…'}
            />
          </label>
          {GEMINI_FIELDS.map(({ key: field, label, placeholder }) => (
            <label key={field} className="block text-xs text-ck-muted">
              {label}
              <input
                value={form[field]}
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                className={inputClass}
                placeholder={placeholder}
                spellCheck={false}
              />
            </label>
          ))}
        </div>
      )}

      {/* Ollama (On-Device) Configuration */}
      {isOllama && (
        <div className="space-y-3 rounded-lg border border-purple-500/30 bg-purple-950/10 p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-purple-200">
                Ollama On-Device Inference
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                  ollamaStatus.running
                    ? 'bg-emerald-500/20 text-emerald-300'
                    : 'bg-amber-500/20 text-amber-300'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    ollamaStatus.running ? 'bg-emerald-400' : 'bg-amber-400'
                  }`}
                />
                {ollamaStatus.running ? 'Connected' : 'Not running'}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => refreshOllama().catch(() => {})}
                disabled={checkingOllama}
                className="rounded bg-white/10 px-2 py-0.5 text-[11px] text-ck-text hover:bg-white/15"
              >
                {checkingOllama ? 'Checking…' : 'Refresh'}
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, ...OLLAMA_DEFAULTS })}
                className="rounded bg-white/10 px-2 py-0.5 text-[11px] text-ck-text hover:bg-white/15"
              >
                Reset defaults
              </button>
            </div>
          </div>

          {/* Low/Mid-tier Laptop Optimization Recommendation */}
          <div className="rounded border border-purple-500/20 bg-black/40 p-2.5 text-xs">
            <div className="flex items-center gap-1.5 font-medium text-purple-200">
              <span>⚡</span>
              <span>Laptop Optimization Guide (Zero Lag):</span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-purple-200/80">
              For low or mid-tier laptops, 3B models offer instant responses, smart thinking, native
              tool calling, and RAG without taxing your CPU/RAM (~2 GB usage). For older 8GB-RAM laptops,
              select 1.5B (~1 GB usage).
            </p>
            <div className="mt-2.5 grid grid-cols-1 gap-1.5 sm:grid-cols-3">
              {RECOMMENDED_MODELS.map((m) => {
                const isSelected = form.ollama_model === m.id
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setForm({ ...form, ollama_model: m.id })}
                    className={`flex flex-col items-start rounded border p-2 text-left transition-colors ${
                      isSelected
                        ? 'border-purple-400 bg-purple-600/30 text-white'
                        : 'border-white/10 bg-white/5 text-ck-muted hover:border-white/20 hover:text-ck-text'
                    }`}
                  >
                    <div className="flex w-full items-center justify-between">
                      <span className="font-semibold text-xs text-ck-text">{m.label}</span>
                      <span className="rounded bg-white/10 px-1 py-0.2 text-[9px] text-purple-300">
                        {m.badge}
                      </span>
                    </div>
                    <span className="mt-1 text-[10px] leading-tight text-ck-muted">{m.desc}</span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Installed Ollama Models Selector */}
          {ollamaStatus.running && ollamaStatus.models.length > 0 && (
            <label className="block text-xs text-ck-muted">
              Select from installed Ollama models
              <select
                value={form.ollama_model}
                onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
                className={inputClass}
              >
                {ollamaStatus.models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} {m.size > 0 ? `(${(m.size / 1e9).toFixed(1)} GB)` : ''}
                  </option>
                ))}
              </select>
            </label>
          )}

          {/* Model Name Input */}
          <label className="block text-xs text-ck-muted">
            Ollama model name (used for router, ask, and agent tools)
            <input
              value={form.ollama_model}
              onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
              className={inputClass}
              placeholder={OLLAMA_DEFAULTS.ollama_model}
              spellCheck={false}
            />
          </label>

          {/* Base URL */}
          <label className="block text-xs text-ck-muted">
            Ollama OpenAI endpoint (default: http://127.0.0.1:11434/v1)
            <input
              value={form.ollama_base_url}
              onChange={(e) => setForm({ ...form, ollama_base_url: e.target.value })}
              className={inputClass}
              placeholder={OLLAMA_DEFAULTS.ollama_base_url}
              spellCheck={false}
            />
          </label>

          <p className="text-[11px] text-ck-muted">
            ✓ 100% private, on-device local inference. No API key needed.
          </p>
        </div>
      )}

      {/* General Settings */}
      <div className="space-y-2 border-t border-white/10 pt-3">
        <label className="block text-xs text-ck-muted">
          Translate to (default language)
          <input
            value={form.translate_language}
            onChange={(e) => setForm({ ...form, translate_language: e.target.value })}
            className={inputClass}
            placeholder="English"
            spellCheck={false}
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-ck-muted">
          <input
            type="checkbox"
            checked={form.debug_log_prompts}
            onChange={(e) => setForm({ ...form, debug_log_prompts: e.target.checked })}
          />
          Log outgoing prompts (debug — check redaction in sidecar.log)
        </label>
        <label className="flex items-center gap-2 text-xs text-ck-muted">
          <input
            type="checkbox"
            checked={form.proactive_suggestions}
            onChange={(e) => setForm({ ...form, proactive_suggestions: e.target.checked })}
          />
          Proactive suggestions (watch managed processes for crashes and errors)
        </label>
      </div>

      <div className="space-y-2 border-t border-white/10 pt-3">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-ck-text">Voice Input (Ctrl+Alt+V)</h3>
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
              voiceStatus === 'ready'
                ? 'bg-emerald-500/20 text-emerald-300'
                : voiceStatus === 'loading'
                  ? 'bg-amber-500/20 text-amber-300'
                  : voiceStatus === 'error'
                    ? 'bg-red-500/20 text-red-300'
                    : 'bg-white/10 text-ck-muted'
            }`}
          >
            Whisper base.en: {voiceStatus}
          </span>
        </div>
        <label className="flex items-center gap-2 text-xs text-ck-muted">
          <input
            type="checkbox"
            checked={form.voice_enabled}
            onChange={(e) => setForm({ ...form, voice_enabled: e.target.checked })}
          />
          Enable voice input
        </label>
        <label className="block text-xs text-ck-muted">
          Microphone
          <select
            value={form.voice_device ?? ''}
            onChange={(e) => setForm({ ...form, voice_device: e.target.value || null })}
            className={inputClass}
          >
            <option value="">Default input device</option>
            {devices.map((d) => (
              <option key={d.id} value={String(d.id)}>
                {d.name} {d.is_default ? '(Default)' : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="space-y-2 border-t border-white/10 pt-3">
        <h3 className="text-xs font-semibold text-ck-text">Browser Automation</h3>
        <label className="block text-xs text-ck-muted">
          Browser channel (installed Edge or Chrome)
          <select
            value={form.browser_channel}
            onChange={(e) => setForm({ ...form, browser_channel: e.target.value })}
            className={inputClass}
          >
            <option value="msedge">Microsoft Edge (msedge)</option>
            <option value="chrome">Google Chrome (chrome)</option>
          </select>
        </label>
        <label className="block text-xs text-ck-muted">
          Search URL template
          <input
            value={form.browser_search_url}
            onChange={(e) => setForm({ ...form, browser_search_url: e.target.value })}
            className={inputClass}
            placeholder="https://www.google.com/search?q={query}"
            spellCheck={false}
          />
        </label>
        <label className="block text-xs text-ck-muted">
          Sensitive domains (always require approval, one per line)
          <textarea
            value={sensitiveText}
            onChange={(e) => setSensitiveText(e.target.value)}
            className={`${inputClass} h-20 resize-none font-mono text-[11px]`}
            spellCheck={false}
          />
        </label>
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleOpenBrowser}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/15"
          >
            Open Clickachu browser
          </button>
          <span className="text-xs text-ck-muted">{browserNote}</span>
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-white/10 pt-3">
        <button type="submit" className={buttonClass}>
          Save
        </button>
        <span className="text-xs text-ck-muted">{note}</span>
      </div>
    </form>
  )
}

function TodayUsageSection() {
  const todayUsage = useStore((s) => s.todayUsage)
  return (
    <section className="space-y-2 border-t border-white/10 pt-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-ck-text">Today's Token &amp; Cost Total</h3>
        <span className="text-[11px] text-ck-muted">{todayUsage.date}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded border border-ck-border bg-black/30 p-2.5">
          <span className="text-[10px] uppercase tracking-wide text-ck-muted">Total Tokens</span>
          <p className="mt-1 font-mono text-base font-semibold text-ck-text">
            {todayUsage.totalTokens.toLocaleString()}
          </p>
          <span className="text-[10px] text-ck-muted">
            {todayUsage.inputTokens.toLocaleString()} in · {todayUsage.outputTokens.toLocaleString()} out
          </span>
        </div>
        <div className="rounded border border-ck-border bg-black/30 p-2.5">
          <span className="text-[10px] uppercase tracking-wide text-ck-muted">Estimated Cost</span>
          <p className="mt-1 font-mono text-base font-semibold text-emerald-400">
            ${todayUsage.costUsd.toFixed(5)}
          </p>
          <span className="text-[10px] text-ck-muted">sum of run.finished</span>
        </div>
      </div>
    </section>
  )
}

export function SettingsTab() {
  const [rootsRevision, setRootsRevision] = useState(0)
  return (
    <div className="min-h-0 flex-1 space-y-6 overflow-y-auto pr-1">
      <section className="space-y-2">
        <h2 className="text-sm font-medium">Models &amp; API</h2>
        <SettingsForm />
        <TodayUsageSection />
      </section>
      <ProjectsSection onRootsChanged={() => setRootsRevision((n) => n + 1)} />
      <RootsSection revision={rootsRevision} />
    </div>
  )
}
