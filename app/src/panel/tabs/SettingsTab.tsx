/** Settings tab: model config + API key, browser settings, voice, projects and allowed folders. */
import { type FormEvent, useEffect, useState } from 'react'
import { api } from '@shared/api'
import type { AudioDevice, Settings } from '@shared/protocol'
import { useStore } from '@shared/store'
import { buttonClass, errorText, inputClass } from '../ui'
import { ProjectsSection } from './ProjectsSection'
import { RootsSection } from './RootsSection'
import { AgentSettings } from './AgentSettings'
import { OllamaSettings } from './OllamaSettings'
import { isLocalOllama, normalizeOllamaUrl } from '@shared/ollama'

const GEMINI_DEFAULTS = {
  router_model: 'gemini-2.5-flash-lite',
  agent_model: 'gemini-2.5-pro',
  base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
} as const

/** Two tiers on one provider: a capable thinking model and a small fast model for easy jobs. */
const GEMINI_FIELDS: {
  key: 'router_model' | 'agent_model' | 'base_url'
  label: string
  placeholder?: string
}[] = [
  {
    key: 'agent_model',
    label: 'Thinking model (tasks, questions, explanations)',
    placeholder: GEMINI_DEFAULTS.agent_model,
  },
  {
    key: 'router_model',
    label: 'Fast model (summaries, translation, rewrites, routing, memory)',
    placeholder: GEMINI_DEFAULTS.router_model,
  },
  { key: 'base_url', label: 'Base URL', placeholder: GEMINI_DEFAULTS.base_url },
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
  useEffect(() => {
    if (connection !== 'connected') return
    api
      .getSettings()
      .then((view) => {
        setForm(view)
        setHasKey(view.has_openrouter_key)
        setVoiceStatus(view.voice_model_status || 'unloaded')
        setSensitiveText(view.sensitive_domains.join('\n'))
      })
      .catch((err: unknown) => setNote(errorText(err)))

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
        agent_model: form.agent_model,
        base_url: form.base_url,
        ...(form.provider === 'ollama'
          ? { ollama_base_url: normalizeOllamaUrl(form.ollama_base_url) }
          : {}),
        ollama_local_profile: form.ollama_local_profile,
        ollama_remote_profile: form.ollama_remote_profile,
        ollama_model: form.ollama_model,
        ollama_summary_model: form.ollama_summary_model,
        ollama_cpu_only: form.ollama_cpu_only,
        translate_language: form.translate_language,
        debug_log_prompts: form.debug_log_prompts,
        auto_web_lookup: form.auto_web_lookup,
        code_execution_enabled: form.code_execution_enabled,
        host_execution_enabled: form.host_execution_enabled,
        agent_max_steps: form.agent_max_steps,
        agent_max_seconds: form.agent_max_seconds,
        agent_max_output_tokens: form.agent_max_output_tokens,
        agent_max_failures: form.agent_max_failures,
        use_model_router: form.use_model_router,
        vision_mode: form.vision_mode,
        reasoning_effort: form.reasoning_effort,
        browser_channel: form.browser_channel,
        browser_search_url: form.browser_search_url,
        proactive_suggestions: form.proactive_suggestions,
        voice_enabled: form.voice_enabled,
        voice_device: form.voice_device,
        sensitive_domains: domains,
      })
      setHasKey(view.has_openrouter_key)
      setVoiceStatus(view.voice_model_status || 'unloaded')
      setForm(view)
      setNote(
        view.ollama_environment_warning ||
          (view.provider === 'ollama' && isLocalOllama(view.ollama_base_url)
            ? 'Saved. Local Ollama selected; OLLAMA_HOST cleared for Clickachu and the Windows user environment. Existing terminals need the cleanup command below.'
            : 'Saved.'),
      )
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
            onClick={() => setForm({ ...form, provider: 'ollama' })}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-all ${
              isOllama
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-ck-muted hover:bg-white/5 hover:text-ck-text'
            }`}
          >
            <span>💻 Ollama (Local / Remote)</span>
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

      {isOllama && <OllamaSettings form={form} onChange={setForm} />}

      <AgentSettings form={form} onChange={setForm} />

      {/* General Settings */}
      <div className="space-y-2 border-t border-white/10 pt-3">
        <label className="flex items-center gap-2 text-xs text-ck-muted">
          <input
            type="checkbox"
            checked={form.auto_web_lookup}
            onChange={(e) => setForm({ ...form, auto_web_lookup: e.target.checked })}
          />
          Look up factual topics online automatically
        </label>
        <p className="text-[11px] text-ck-muted">
          Sends search terms to a public search engine in the background. No browser window is
          opened.
        </p>
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
            {todayUsage.inputTokens.toLocaleString()} in ·{' '}
            {todayUsage.outputTokens.toLocaleString()} out
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
